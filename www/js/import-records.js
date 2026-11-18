/* Sanguity · import page, "Type them in" tab: records other than lab results (visits, stays, medications, claims,
   documents...), one form each, picked by kind and saved with the typed lab results as one import
   (server/routes_manual.py, server/manual_items.py). The page sends what's typed as {"kind", field: text}; the server
   reads it and says, per form and per field, what it became or what's wrong, and import-manual.js shows it here
   through window.sanguityRecords. A document's file goes first through the upload routes (one upload per file, so
   through nginx's upload route and its limits); the form then names the upload and the file.
   Plain script after util.js and before import-manual.js; its own names stay inside this function. */
"use strict";
(function () {
  const DATE = "YYYY-MM-DD", OPT = "optional";
  const CATS = ["Doctor visits", "Hospital stay (facility)", "Hospital outpatient (facility)", "Hospital physician care", "Lab & pathology",
    "Imaging & tests", "Procedures", "Chemo, infusions & drugs", "Radiation therapy", "Physical therapy", "Home health",
    "Equipment & supplies", "Pharmacy", "Other medical"];
  const STUDY = what => [["date", "Date", "date"], ["time", "Time", "time"], ["title", "Title", "text", what], ["where", "Where"],
    ["who", "Who", "text", "Doctor or reader"], ["text", "Findings or impression", "long"]];
  const UNITS = {temp_unit: [["F", "°F"], ["C", "°C"]], height_unit: [["in", "in"], ["cm", "cm"]], weight_unit: [["lb", "lb"], ["kg", "kg"]]};
  // kind -> [label, fields]; a field is [name, caption, type (text, date, time, long, money, number, select, file), placeholder or options]
  const KINDS = {
    visit: ["Visit", [["date", "Date", "date"], ["time", "Time", "time"], ["title", "Title", "text", "Office visit"], ["who", "Who", "text", "Doctor or nurse"],
      ["where", "Where"], ["dx", "Diagnoses", "long"], ["note", "Note", "long"]]],
    stay: ["Hospital stay", [["admit", "Admitted", "date"], ["admit_time", "Time", "time"], ["discharge", "Discharged", "date"],
      ["discharge_time", "Time", "time"], ["where", "Hospital"], ["how", "How admitted", "select", [["", "–"], ["Emergency", "Emergency"],
      ["Planned", "Planned"], ["Transfer", "Transfer"], ["Observation", "Observation"], ["Other", "Other"]]], ["note", "Discharge note", "long"]]],
    procedure: ["Procedure", STUDY("Biopsy, surgery…")],
    imaging: ["Imaging", STUDY("CT chest, X-ray…")],
    study: ["Report or study", STUDY("Pathology report…")],
    med: ["Medication", [["name", "Medication", "text", "Name and strength"], ["instructions", "Dose and instructions", "text", "1 tablet daily"],
      ["start", "Started", "date"], ["stop", "Stopped", "date", OPT], ["prescriber", "Prescribed by"],
      ["status", "Status", "select", [["", "–"], ["Active", "Active"], ["Stopped", "Stopped"], ["On hold", "On hold"], ["Completed", "Completed"]]]]],
    rx: ["Pharmacy fill", [["date", "Filled", "date"], ["drug", "Drug"], ["pharmacy", "Pharmacy"], ["supply", "Days' supply", "number", OPT],
      ["billed", "Billed", "money", OPT], ["paid", "Insurance paid", "money", OPT], ["owe", "You paid", "money", OPT], ["insurer", "Insurer", "text", OPT]]],
    problem: ["Problem or diagnosis", [["name", "Problem or diagnosis"], ["status", "Status", "select", [["Active", "Active"], ["Resolved", "Resolved"], ["Inactive", "Inactive"]]],
      ["since", "Since", "date", OPT], ["type", "Type", "select", [["Diagnosis", "Diagnosis"], ["Problem", "Problem"], ["Allergy", "Allergy"]]]]],
    immunization: ["Immunization", [["date", "Date given", "date"], ["vaccine", "Vaccine", "text", "Flu shot, Tdap…"],   // #179
      ["dose", "Dose number", "text", OPT], ["site", "Site", "text", "Left arm"], ["lot", "Lot number", "text", OPT],
      ["who", "Given by", "text", OPT], ["where", "Where", "text", "Clinic or pharmacy"]]],
    claim: ["Insurance claim", [["date", "Date of service", "date"], ["provider", "Provider"], ["title", "Service", "text", "Office visit"],
      ["billed", "Billed", "money"], ["paid", "Plan paid", "money"], ["owe", "You owe", "money"],
      ["status", "Status", "select", [["", "–"], ["Paid", "Paid"], ["Processed", "Processed"], ["Pending", "Pending"], ["Denied", "Denied"]]],
      ["insurer", "Insurer"], ["claim", "Claim #", "text", OPT], ["category", "Type of care", "select", [["", "Work it out"], ...CATS.map(c => [c, c])]]]],
    payment: ["Out-of-pocket payment", [["date", "Date", "date"], ["payee", "Paid to"], ["amount", "Amount", "money"],
      ["group", "For", "select", ["Medical bills", "Pharmacy", "Health insurance premiums", "Dental", "Vision", "Equipment & supplies", "Other medical"].map(g => [g, g])],
      ["account", "Paid from", "text", OPT], ["hsa", "HSA or FSA", "select", [["", "Neither"], ["HSA", "HSA"], ["FSA", "FSA"]]],
      ["memo", "Memo", "text", OPT], ["file", "Receipt (optional)", "file"]]],   // #55: the receipt is kept as a document
    upcoming: ["Upcoming appointment", [["date", "Date", "date"], ["time", "Time", "time"], ["title", "Title", "text", "Follow-up visit"],
      ["who", "Who"], ["where", "Where"]]],
    vitals: ["Vitals", [["date", "Date", "date"], ["time", "Time", "time"], ["where", "Where", "text", OPT], ["bp", "Blood pressure", "text", "120/80"],
      ["hr", "Heart rate", "number", "bpm"], ["temp", "Temperature", "number"], ["temp_unit", "Unit", "select", UNITS.temp_unit],
      ["height", "Height", "number"], ["height_unit", "Unit", "select", UNITS.height_unit], ["spo2", "SpO2", "number", "%"],
      ["resp", "Breathing rate", "number", "/min"], ["weight", "Weight", "number"], ["weight_unit", "Unit", "select", UNITS.weight_unit]]],
    // ---- readings taken at home (#224): beside the phone's on Body and activity, as "Typed in"; a height into About this person
    home_bp: ["Blood pressure at home", [["date", "Date", "date"], ["time", "Time", "time"], ["sys", "Systolic", "number", "mmHg"],
      ["dia", "Diastolic", "number", "mmHg"], ["pulse", "Pulse", "number", "bpm, optional"]]],
    home_glucose: ["Blood glucose at home", [["date", "Date", "date"], ["time", "Time", "time"], ["glucose", "Glucose", "number"],
      ["glucose_unit", "Unit", "select", [["mg/dL", "mg/dL"], ["mmol/L", "mmol/L"]]],
      ["meal", "Meal (optional)", "select", [["", "Not noted"], ["before", "Before a meal"], ["after", "After a meal"], ["other", "Other"]]]]],
    home_temp: ["Temperature at home", [["date", "Date", "date"], ["time", "Time", "time"], ["temp", "Temperature", "number"],
      ["temp_unit", "Unit", "select", UNITS.temp_unit]]],
    home_spo2: ["Blood oxygen at home", [["date", "Date", "date"], ["time", "Time", "time"], ["spo2", "SpO2", "number", "%"]]],
    home: ["Weight or steps at home", [["date", "Date", "date"], ["weight", "Weight", "number"], ["weight_unit", "Unit", "select", UNITS.weight_unit],
      ["steps", "Steps", "number"]]],
    height: ["Height", [["date", "Date measured", "date"], ["height", "Height", "text"],
      ["height_unit", "Unit", "select", [["cm", "cm"], ["in", "ft and in"]]]]],
    workout: ["Workout", [["date", "Date", "date"], ["time", "Start", "time"], ["minutes", "Minutes", "number"], ["activity", "Activity", "text", "Walk, swim…"]]],
    document: ["Letter or document", [["date", "Date", "date"], ["time", "Time", "time"], ["title", "Title"],
      ["doctype", "Kind", "select", [["document", "Document"], ["letter", "Letter"]]], ["where", "From", "text", OPT], ["file", "File", "file"]]],
  };
  // #224: what a home reading's form is for, under its name; and its units as this person chose them (Settings → Units)
  const HOME = "Shown on Body and activity beside the phone's readings, as typed in. To correct one, type it again for the same date and time.";
  const HINTS = {home_bp: HOME, home_glucose: HOME, home_temp: HOME, home_spo2: HOME,
    height: "Goes into the height history under Sharing → About this person, where it can be changed or removed. In feet and inches, write it like 5 ft 8 in."};
  const PREF = {glucose_unit: u => u.lab === "si" ? "mmol/L" : u.lab === "conventional" ? "mg/dL" : null,
    temp_unit: u => u.temperature === "C" || u.temperature === "F" ? u.temperature : null,
    height_unit: u => u.height === "ftin" ? "in" : u.height === "cm" ? "cm" : null, weight_unit: u => u.weight === "kg" || u.weight === "lb" ? u.weight : null};
  let units = {}, unitsFor = null;
  let loading = null;
  function loadUnits() {   // the profile's units setting, once per profile; until then (or without it) the forms' own defaults
    const p = +$("#profile").value;
    if (!p) return Promise.resolve();
    if (unitsFor === p) return loading;
    unitsFor = p; units = {};
    return loading = api(`profiles/${p}/settings`).then(r => r.ok ? r.json() : {}).then(j => { if (unitsFor === p) units = j.units || {}; }).catch(() => {});
  }
  function inUnits(card) {
    if (HINTS[card.dataset.kind]) for (const s of card.querySelectorAll("select[data-f$=_unit]")) {
      const want = (PREF[s.dataset.f] || (() => null))(units);
      if (want) s.value = want;
    }
  }
  const MAX_LEN = {long: 4000};
  const ACCEPT = ".pdf,.png,.jpg,.jpeg,.gif,application/pdf,image/png,image/jpeg,image/gif";
  const box = $("#manItems"), picker = $("#manKind");
  let seq = 0, changed = () => {};

  const pid = () => +$("#profile").value;
  const today = zToday;   // dates.js
  const cards = () => [...box.querySelectorAll(".man-item")];
  const size = n => n >= 2 ** 20 ? (n / 2 ** 20).toFixed(1) + " MB" : n >= 1024 ? Math.round(n / 1024) + " KB" : n + " bytes";

  picker.append(...Object.entries(KINDS).map(([k, [label]]) => el("option", {value: k, text: label})));

  function field(card, [f, cap, type = "text", extra]) {
    const id = `rec${card.dataset.seq}-${f}`, err = el("span", {class: "man-err", id: id + "-err"});
    let input;
    if (type === "select") input = el("select", {}, ...extra.map(([v, t]) => el("option", {value: v, text: t})));
    else if (type === "long") input = el("textarea", {rows: 2, maxlength: MAX_LEN.long});
    else if (type === "file") input = el("input", {type: "file", accept: ACCEPT});
    else input = el("input", {type: "text", autocomplete: "off", maxlength: 300,
      placeholder: type === "date" ? (extra || DATE) : type === "time" ? (extra || OPT) : extra || null,
      inputmode: type === "money" || type === "number" ? "decimal" : null});
    Object.assign(input, {id}); input.dataset.f = f;
    input.setAttribute("aria-describedby", id + "-err");
    return el("label", {class: `rec-f rec-${type}` + (f === "file" ? " rec-wide" : ""), for: id}, el("span", {class: "cap", text: cap}), input,
      f === "file" ? el("span", {class: "man-file", "aria-live": "polite"}) : null, err);
  }

  function add(kind, focus = true) {
    const [label, fields] = KINDS[kind];
    const last = cards().pop(), from = last ? last.querySelector("[data-f=date],[data-f=admit],[data-f=start]") : null;
    const card = el("fieldset", {class: "man-item", "data-kind": kind, "data-seq": ++seq});
    card.append(...present(el("legend", {text: label}), HINTS[kind] ? el("p", {class: "hint rec-hint", text: HINTS[kind]}) : null,
      el("div", {class: "rec-grid"}, fields.map(x => field(card, x))),
      el("button", {type: "button", class: "del", "aria-label": `Remove this ${label.toLowerCase()}`, title: "Remove", text: "✕"}),
      el("div", {class: "man-check", "aria-live": "polite"})));
    box.append(card);
    // the first date starts on the last record's, else today: records typed from one letter share it
    const first = card.querySelector("[data-f=date],[data-f=admit],[data-f=start]");
    if (first) first.value = from && from.value ? from.value : today();
    inUnits(card);
    card.dataset.initial = JSON.stringify(read(card));
    if (HINTS[kind]) loadUnits().then(() => {   // the units known only now: an untouched form takes them
      if (!card.isConnected || JSON.stringify(read(card)) !== card.dataset.initial) return;
      inUnits(card); card.dataset.initial = JSON.stringify(read(card));
    });
    number();
    if (focus) (card.querySelector("input:not([data-f=date]):not([data-f=admit]):not([data-f=start]), textarea") || first).focus();
    changed();
    return card;
  }
  function number() {   // each form's group is named by its kind and number, so the checks and "Remove" say which
    cards().forEach((c, i) => c.setAttribute("aria-label", `${KINDS[c.dataset.kind][0]}, record ${i + 1}`));
  }
  function read(card) {
    const v = {kind: card.dataset.kind};
    for (const i of card.querySelectorAll("[data-f]")) {
      if (i.dataset.f === "file") { v.upload = card.dataset.upload || ""; v.file = card.dataset.file || ""; }
      else v[i.dataset.f] = i.value.trim();
    }
    return v;
  }
  // a form with only what it started with is left out, as an empty lab row is
  const values = () => cards().map(c => { const v = read(c); return JSON.stringify(v) === c.dataset.initial ? {kind: v.kind} : v; });

  function show(items) {
    const byN = new Map((items || []).map(x => [x.n, x]));
    let ok = 0, bad = 0, dup = 0;
    cards().forEach((c, i) => {
      const x = byN.get(i + 1), out = c.querySelector(".man-check");
      c.querySelectorAll("[data-f]").forEach(inp => inp.removeAttribute("aria-invalid"));
      c.querySelectorAll(".man-err").forEach(e => e.replaceChildren());
      out.classList.toggle("bad", !!(x && !x.ok));
      if (!x) { out.replaceChildren(); return; }
      if (!x.ok) {
        bad++;
        out.replaceChildren("Can't save this: it " + x.problems.join("; ") + ".");
        for (const [f, why] of Object.entries(x.fields || {})) {
          const inp = c.querySelector(`[data-f=${f === "upload" ? "file" : f}]`) || c.querySelector("[data-f]");
          inp.setAttribute("aria-invalid", "true");
          c.querySelector(`#${inp.id}-err`).textContent = "This " + why + ".";
        }
        return;
      }
      ok++;
      if (x.duplicate) dup++;
      out.replaceChildren("✓ ", el("b", {text: x.label}), ` · ${x.summary || ""}`,
        ...(x.duplicate ? [" · ", el("span", {class: "dup", text: "already in this profile: it won't be saved again"})] : []),
        ...(x.notes.length ? [` (${x.notes.join("; ")})`] : []));
    });
    return {ok, bad, dup};
  }

  /* ---------------------------------------------------------------- a document's file: sent as an upload of its own */
  async function call(path, opts) {
    const r = await api(path, opts);
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || "The server answered " + r.status + ".");
    return j;
  }
  function forget(card) {   // an upload no saved record uses: discarded
    const p = +card.dataset.profile, u = card.dataset.upload;
    delete card.dataset.upload; delete card.dataset.file;
    if (u && p && !card.dataset.saved) api(`profiles/${p}/uploads/${u}`, {method: "DELETE"}).catch(() => {});
  }
  async function send(card, file) {
    const note = card.querySelector(".man-file"), p = pid(), mine = String(++seq);
    forget(card);
    card.dataset.sending = mine;
    if (!/\.(pdf|png|jpe?g|gif)$/i.test(file.name)) { note.textContent = "Attach a PDF or a picture (PNG, JPEG or GIF)."; delete card.dataset.sending; changed(); return; }
    note.textContent = `Uploading ${file.name}…`;
    try {
      const up = await call(`profiles/${p}/uploads`, {method: "POST"});
      if (up.limits && file.size > up.limits.file_bytes) throw new Error(`a file can be at most ${size(up.limits.file_bytes)}.`);
      await call(`profiles/${p}/uploads/${up.id}/files?path=${encodeURIComponent(file.name)}`,
        {method: "PUT", body: file, headers: {"Content-Type": "application/octet-stream"}});
      await call(`profiles/${p}/uploads/${up.id}/finish`, {method: "POST"});
      if (card.dataset.sending !== mine || !card.isConnected) { api(`profiles/${p}/uploads/${up.id}`, {method: "DELETE"}).catch(() => {}); return; }
      Object.assign(card.dataset, {upload: String(up.id), file: file.name, profile: String(p)});
      note.textContent = `${file.name} (${size(file.size)}) uploaded.`;
    } catch (e) {
      note.textContent = `Couldn't upload ${file.name}: ${e.message}`;
    } finally {
      if (card.dataset.sending === mine) delete card.dataset.sending;
      changed();
    }
  }

  /* ---------------------------------------------------------------- adding, removing */
  $("#manAddKind").onclick = () => {
    if (picker.value === "labs") { $("#manAdd").click(); return; }
    add(picker.value);
  };
  box.addEventListener("input", () => changed());
  box.addEventListener("change", ev => {
    const f = ev.target.closest("[data-f=file]");
    if (f && f.files[0]) send(f.closest(".man-item"), f.files[0]);
    else changed();
  });
  box.addEventListener("click", ev => {
    const del = ev.target.closest(".del");
    if (!del) return;
    const c = del.closest(".man-item"), next = c.nextElementSibling || c.previousElementSibling;
    forget(c); c.remove(); number();
    (next ? next.querySelector("[data-f]") : picker).focus();
    changed();
  });

  // the Costs tab's "Type in what you paid" (#55) opens sources.html#payment: Type records in, with a payment's form
  if (location.hash === "#payment") { showKind("manual"); add("payment"); }
  $("#profile").addEventListener("change", () => { if (cards().some(c => HINTS[c.dataset.kind])) loadUnits(); });

  window.sanguityRecords = {
    values, show,
    busy: () => cards().some(c => c.dataset.sending),
    count: () => cards().length,
    onchange: f => { changed = f; },
    saved() { cards().forEach(c => { c.dataset.saved = "1"; c.remove(); }); },
    reset() {   // another profile: what's typed stays, but a file was uploaded to the one before, so it's chosen again
      for (const c of cards()) {
        if (!c.dataset.upload && !c.dataset.sending) continue;
        forget(c); delete c.dataset.sending;
        c.querySelector("[data-f=file]").value = "";
        c.querySelector(".man-file").textContent = "Choose the file again: it was sent to the profile chosen before.";
      }
      changed();
    },
  };
})();
