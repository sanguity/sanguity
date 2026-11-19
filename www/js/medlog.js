/* Sanguity · the medication log (#235): doses the person says were taken, taken late or skipped, beside the records.
   The records (GET api/profiles/{pid}/data) list the doses typed in as DATA.doses = [{id, med (the medication's key),
   name, t (wall-clock YYYY-MM-DDTHH:MM), status, note, slot (the reminder time it was logged against), by, at, changed,
   changed_by}] (server/med_log.py); the phone's (Apple Health's Medications, sent by Health Auto Export) come with the
   watch data as WATCH.med_doses = [[time with offset, name, "taken" | "skipped", dosage, units, app]], matched to a
   medication by the same key as the records' (medKeyOf, meds.js) and read only.
   Where they show: the medication's drawer (its log, a weekly count, Log a dose), today's doses (built from #162's
   reminder times when set) on the Medications tab's Today (medications.js, #275), where the log lives, and on the
   Overview, marks on the medication's span in the Timeline's and the Medications tab's lane, and a row under the Labs
   charts when "Doses logged" is ticked (#192's "Show on charts"). A dose is shown by its
   shape as well as its words (taken ●, taken late ◆, skipped ○), never by colour alone.
   It keeps what the person says happened. Nothing here says a dose was due or should be taken: counts are of the
   doses logged ("12 of 14 logged doses taken"). Owners and editors log, correct and remove typed doses.
   Plain script, loaded in order by index.html after meds.js; all files share one global scope. */
"use strict";
const DOSE_ST = [["taken", "Taken"], ["late", "Taken late"], ["skipped", "Skipped"]];
const DOSE_LABEL = Object.fromEntries(DOSE_ST);
let DOSES = [];   // typed and the phone's, oldest first: {id (typed) | null, key, name, t, ts, status, note, slot, phone, app, dose}

function medLogLoad() {
  const typed = (Array.isArray(DATA.doses) ? DATA.doses : []).filter(d => d && DOSE_LABEL[d.status] && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(d.t || ""))
    .map(d => ({...d, key: d.med, ts: zParse(d.t), phone: false}));
  const sent = (typeof WATCH === "object" && WATCH && Array.isArray(WATCH.med_doses) ? WATCH.med_doses : [])
    .filter(r => Array.isArray(r) && (r[2] === "taken" || r[2] === "skipped") && r[1] && isFinite(zParse(String(r[0]))))
    .map(r => ({id: null, key: medKeyOf(r[1]), name: String(r[1]), t: String(r[0]), ts: zParse(String(r[0])), status: r[2], note: "",
      dose: typeof r[3] === "number" ? `${r[3]} ${r[4] || ""}`.trim() : "", phone: true, app: r[5] || "phone"}));
  DOSES = [...typed, ...sent].filter(d => isFinite(d.ts)).sort((a, b) => a.ts - b.ts);
  medLogControls();
}
const dosesOf = m => DOSES.filter(d => d.key === m.key);
const doseWhen = d => fmtDT(d.ts);
const doseSay = d => `${DOSE_LABEL[d.status]}, ${d.name}, ${doseWhen(d)}`;
/* a dose's mark: ● taken, ◆ taken late, ○ skipped */
function doseShape(status, x, y, attrs = {}, r = 3) {
  if (status === "late") return el("svg:path", {d: `M${x},${y - r * 1.3}L${x + r * 1.3},${y}L${x},${y + r * 1.3}L${x - r * 1.3},${y}Z`, class: "dosemark late", ...attrs});
  return el("svg:circle", {cx: x, cy: y, r, class: "dosemark " + status, ...attrs});
}
const doseIcon = status => el("svg:svg", {class: "doseicon", viewBox: "0 0 12 12", width: 12, height: 12, "aria-hidden": "true"}, doseShape(status, 6, 6, {}, 3.6));

/* ---- counting: weeks from Monday, in this person's zone; counts of what was logged, never of what was expected */
const doseDay = d => isoDay(d.ts);
function weekOf(day) { const ts = dayTs(day), dow = (zdate(ts).getDay() + 6) % 7; return isoDay(ts - dow * DAY); }
function doseCounts(list) {
  const n = s => list.filter(d => d.status === s).length;
  return {logged: list.length, taken: n("taken") + n("late"), late: n("late"), skipped: n("skipped")};
}
/* "12 of 14 logged doses taken (1 late), 2 skipped" (server/med_log.said) */
function doseSaid(c) {
  if (!c.logged) return "No doses logged";
  return `${c.taken} of ${c.logged} logged dose${c.logged === 1 ? "" : "s"} taken` + (c.late ? ` (${c.late} late)` : "") + (c.skipped ? `, ${c.skipped} skipped` : "");
}
function doseWeeks(list) {
  const by = new Map();
  for (const d of list) { const w = weekOf(doseDay(d)); if (!by.has(w)) by.set(w, []); by.get(w).push(d); }
  return [...by.entries()].sort((a, b) => b[0].localeCompare(a[0])).map(([w, ds]) => ({week: w, ...doseCounts(ds)}));
}
const thisWeek = list => list.filter(d => weekOf(doseDay(d)) === weekOf(zToday()));

/* ---------------------------------------------------------------- the medication's drawer: its log */
function medLogSection(m) {
  const ds = dosesOf(m), write = canEdit(), cur = mrCurrent(m);
  if (!ds.length && !(write && cur)) return [];
  const out = [el("div", {class: "sect", text: "Doses logged"})];
  if (ds.length) {
    out.push(el("p", {class: "dose-week", text: "This week: " + doseSaid(doseCounts(thisWeek(ds)))}));
    const weeks = doseWeeks(ds).slice(0, 8);
    out.push(scrollBox({class: "tablewrap", "aria-label": "Doses logged, week by week", style: {maxHeight: "none"}},
      el("table", {class: "data doseweeks"}, el("thead", {}, el("tr", {}, ["Week of", "Logged", "Taken", "Of them late", "Skipped"].map(h => el("th", {scope: "col", text: h})))),
        el("tbody", {}, weeks.map(w => el("tr", {}, el("td", {text: fmtD(dayTs(w.week))}), el("td", {text: w.logged}), el("td", {text: w.taken}),
          el("td", {text: w.late}), el("td", {text: w.skipped})))))));
    const list = el("ul", {class: "doselist"}), more = el("button", {class: "btn small", type: "button", hidden: true, "data-whole": ""});   // shown whole: kept so when drawn again (drawer.js drawerKeep)
    const newest = [...ds].reverse(), FEW = 10;
    let all = false;
    const draw = () => {
      list.replaceChildren(...(all ? newest : newest.slice(0, FEW)).map(d => el("li", {class: "dose-item"},
        doseIcon(d.status),
        el("span", {class: "dose-says"}, el("b", {text: DOSE_LABEL[d.status]}), " · " + doseWhen(d) + (d.dose ? ` · ${d.dose}` : "")),
        el("span", {class: "dose-src", text: d.phone ? `From ${d.app}` : `Logged by ${d.by || "someone"}` + (d.slot ? ` · reminder at ${mrTime(d.slot)}` : "")}),
        d.note ? el("span", {class: "dose-note", text: d.note}) : null,
        write && !d.phone ? el("span", {class: "mr-acts"},
          el("button", {class: "btn small", type: "button", text: "Edit", "aria-label": "Edit the dose " + doseSay(d), onclick: () => doseForm(m, d)}),
          el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": "Remove the dose " + doseSay(d), onclick: async () => {
            if (!await ask({title: "Remove this dose from the log?", message: doseSay(d) + ".", action: "Remove", danger: true})) return;
            doseSend(`/${d.id}`, {method: "DELETE"}, msg, {med: m.key});
          }})) : null)));
      more.hidden = newest.length <= FEW;
      more.textContent = all ? "Show fewer" : `Show all ${newest.length}`; more.dataset.whole = all ? "1" : "";
    };
    more.onclick = () => { all = !all; draw(); };
    draw();
    out.push(list, more);
  }
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  if (write && cur) out.push(el("div", {class: "btns"}, el("button", {class: "btn", type: "button", text: "Log a dose", onclick: () => doseForm(m, null)})));
  out.push(el("p", {class: "hint", text: "What was logged, as it was said: taken, taken late or skipped. A record kept for the person, not medical advice."}), msg);
  return out;
}

/* the log / correct form, in the drawer. d: the dose (null: a new one); preset: {status, slot} */
function doseForm(m, d, preset = {}) {
  const body = drawerHead("med", d ? "Correct a dose" : "Log a dose", m.name);
  const uid = Math.random().toString(36).slice(2, 8), id = n => `dose-${n}-${uid}`;
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  let status = d ? d.status : preset.status || "taken";
  const seg = el("div", {class: "seg dose-st", role: "radiogroup", "aria-label": "What happened"});
  const set = s => { status = s; for (const b of seg.children) { b.setAttribute("aria-checked", b.dataset.st === s); b.tabIndex = b.dataset.st === s ? 0 : -1; } };
  seg.append(...DOSE_ST.map(([s, t]) => el("button", {type: "button", role: "radio", "data-st": s, text: t, onclick: () => set(s),
    onkeydown: ev => { const i = DOSE_ST.findIndex(([x]) => x === status), k = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[ev.key];
      if (k) { ev.preventDefault(); const n = DOSE_ST[(i + k + DOSE_ST.length) % DOSE_ST.length][0]; set(n); seg.querySelector(`[data-st="${n}"]`).focus(); } }})));
  set(status);
  const now = zdate(Date.now()), hhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  const date = el("input", {type: "date", id: id("date"), required: true, max: zToday()}), time = el("input", {type: "time", id: id("time"), required: true});
  date.value = d ? d.t.slice(0, 10) : zToday(); time.value = d ? d.t.slice(11, 16) : hhmm;
  const note = el("textarea", {id: id("note"), rows: 3, maxlength: 1000}); note.value = d ? d.note || "" : "";
  const row = (lbl, input) => el("div", {class: "jfield"}, el("label", {for: input.id, text: lbl}), input);
  const form = el("form", {class: "jform", novalidate: true},
    el("div", {class: "jfield"}, el("span", {class: "jlabel", text: "What happened"}), seg),
    el("div", {class: "jwhen"}, row("Date", date), row("Time", time)), row("Note (optional)", note),
    el("div", {class: "btns"}, el("button", {class: "btn primary", type: "submit", text: d ? "Save" : "Log dose"}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => openMedication(m)})), msg);
  form.onsubmit = ev => {
    ev.preventDefault();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date.value) || !/^\d{2}:\d{2}/.test(time.value)) { sayIn(msg, "Give the date and time.", true); (date.value ? time : date).focus(); return; }
    const out = {med: m.key, t: `${date.value}T${time.value.slice(0, 5)}`, status, note: note.value, slot: d ? d.slot : preset.slot || null};
    doseSend(d ? `/${d.id}` : "", {method: d ? "PUT" : "POST", body: JSON.stringify(out)}, msg, {med: m.key});
  };
  body.append(form);
  showDrawer();
  seg.querySelector("[aria-checked=true]").focus({preventScroll: true});
}
/* every change: the reply's dose put in the records in place (store.js, #271), which draws again what shows the
   doses (today's, the lanes, the charts), and the medication's drawer drawn again (after: {med}); logged from today's
   doses (after: {row}, its row's data-dose-row, in the tab shown: the Medications tab's or the Overview's, #275), focus
   goes to the row's medication once its buttons are gone */
async function doseSend(path, opts, msg, after) {
  sayIn(msg, "Saving…");
  let r, j;
  try { r = await api(`profiles/${PROFILE.id}/medications/doses${path}`, opts); j = await r.json().catch(() => ({})); }
  catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); return; }
  if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save the dose (${r.status}).`, true); return; }
  storePut("doses", j);
  if (after.med) medAgain(MEDS.find(x => x.key === after.med));
  else if (!document.activeElement || document.activeElement === document.body) document.querySelector(`#view-${state.tab} [data-dose-row="${CSS.escape(after.row)}"] .dose-med`)?.focus({preventScroll: true});
  announce(opts.method === "DELETE" ? "Dose removed from the log." : "Dose logged.");
}

/* ---------------------------------------------------------------- today's doses: the Medications tab's and the Overview's
   For each current medication: a row for each of today's reminder times (#162: reminders on, today one of their days,
   between from and their end), or one row when it has none; each logged dose of today beside its row. Owners and
   editors log with one click (the time is now); viewers see what was logged. None for a profile without current
   medications, or for a viewer when nothing was logged today. A card: id and head, its own (the Medications tab's
   Today, #275: medications.js), else the Overview's; any: what a medication with no reminder times today says for its time */
function todayDoses({id = "ovDoses", head = el("h3", {text: "Today's medication doses"}), any = "Today"} = {}) {
  const write = canEdit(), today = zToday(), dow = MR_DAYS[(zdate(Date.now()).getDay() + 6) % 7][0];
  const cur = MEDS.filter(mrCurrent);
  const todays = DOSES.filter(d => doseDay(d) === today);
  if (!cur.length || !write && !todays.length) return null;
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  const now = () => { const z = zdate(Date.now()); return `${today}T${String(z.getHours()).padStart(2, "0")}:${String(z.getMinutes()).padStart(2, "0")}`; };
  const log = (m, status, slot) => doseSend("", {method: "POST", body: JSON.stringify({med: m.key, t: now(), status, slot: slot || null, note: ""})}, msg, {row: m.key + " " + (slot || "")});
  const quick = (m, slot, what) => el("span", {class: "dose-quick", role: "group", "aria-label": `Log ${what}`}, ...DOSE_ST.map(([s, t]) =>
    el("button", {class: "btn small", type: "button", text: t, "aria-label": `${t}: ${what}`, onclick: () => log(m, s, slot)})));
  const logged = d => el("span", {class: "dose-done"}, doseIcon(d.status), `${DOSE_LABEL[d.status]} · ${fmtTime(d.ts)}` + (d.phone ? ` · from ${d.app}` : ""));
  const rows = [];
  for (const m of cur) {
    const mine = todays.filter(d => d.key === m.key);
    const slots = [...new Set(mrOf(m).filter(r => r.on && r.days.includes(dow) && r.from <= today && !(mrEnds(r, m) && mrEnds(r, m).day < today)).flatMap(r => r.times))].sort();
    const open = () => el("button", {class: "cell-btn dose-med", type: "button", text: m.name, "data-tip": "Its details and log", onclick: () => openMedication(m)});
    for (const slot of slots) {
      const got = mine.filter(d => d.slot === slot);
      const what = `${m.name}, reminder at ${mrTime(slot)}`;
      rows.push(el("li", {class: "dose-today", "data-dose-row": m.key + " " + slot}, el("span", {class: "dose-slot", text: mrTime(slot)}), open(),
        got.length ? el("span", {class: "dose-got"}, ...got.map(logged)) : write ? quick(m, slot, what) : el("span", {class: "hint", text: "Nothing logged"})));
    }
    const other = mine.filter(d => !slots.includes(d.slot));
    if (!slots.length || other.length) rows.push(el("li", {class: "dose-today", "data-dose-row": m.key + " "}, el("span", {class: "dose-slot", text: slots.length ? "Also" : any}), open(),
      el("span", {class: "dose-got"}, ...other.map(logged), write ? quick(m, null, m.name) : null)));
  }
  const week = thisWeek(DOSES.filter(d => cur.some(m => m.key === d.key)));
  return el("div", {class: "card", id}, head,
    el("p", {class: "hint", text: write ? "Log what happened, for your own record: taken, taken late or skipped." : "What was logged today."}),
    el("ul", {class: "doselist today"}, ...rows),
    el("p", {class: "dose-week", text: "This week: " + doseSaid(doseCounts(week))}),
    moreAbout("The times are those of the reminders set on each medication (its details, on the Medications tab). This keeps what is logged, nothing more. Doses logged in Apple Health's Medications come with the phone's data when Health Auto Export sends them. This isn't medical advice."),
    msg);
}

/* ---------------------------------------------------------------- the Timeline: marks on each medication's span */
function medDoseMarks(g, m, x, y, L, R) {
  dosesOf(m).forEach(d => {
    const i = DOSES.indexOf(d), dx = x(d.ts);
    if (dx < L - 3 || dx > R + 3) return;
    g.push(doseShape(d.status, dx, y, {"data-med": m.i, "data-dose": i}, 3.4));
  });
}
function doseTip(m, i, ev) {
  const d = DOSES[i]; if (!d) return hideTip();
  showTip([el("div", {class: "v", text: DOSE_LABEL[d.status]}), el("div", {class: "s", text: `${m.name} · ${doseWhen(d)}`}),
    d.note ? el("div", {class: "s", text: d.note}) : null, el("div", {class: "s", text: d.phone ? `From ${d.app}` : "Logged in Sanguity"})], ev);
}

/* ---------------------------------------------------------------- Labs: one row of doses under each chart, when ticked */
let dosesOnCharts = false;
try { dosesOnCharts = localStorage.getItem("sg-doses-charts") === "1"; } catch (e) {}
const dosesInWindow = () => dosesOnCharts && state.cats.has("med") ? DOSES.filter(d => d.ts >= state.from && d.ts <= state.to) : [];
CHART_LAYERS.push({
  space: () => dosesInWindow().length ? 18 : 0,
  draw(g, x, top, R) {
    const y = top + 9;
    g.push(el("svg:text", {class: "jrow-label", x: 46, y: y + 3.5, "text-anchor": "end", text: "Doses"}));
    for (const d of dosesInWindow()) {
      const dx = x(d.ts); if (dx < 50 || dx > R + 3) continue;
      const m = MEDS.find(m => m.key === d.key);
      const node = doseShape(d.status, dx, y, {tabindex: 0, role: "button", "aria-label": doseSay(d)}, 3.4);
      node.addEventListener("pointermove", ev => { ev.stopPropagation(); if (m) doseTip(m, DOSES.indexOf(d), ev); });
      node.addEventListener("pointerleave", hideTip);
      node.addEventListener("click", ev => { ev.stopPropagation(); hideTip(); if (m) openMedication(m); });
      node.addEventListener("keydown", ev => { if ((ev.key === "Enter" || ev.key === " ") && m) { ev.preventDefault(); openMedication(m); } });
      g.push(node);
    }
  },
});
/* the tick beside Medications under "Show on charts", and its legend: only while there are doses */
function medLogControls() {
  const meds = $("#optMedsLabel"), legend = document.querySelector("#view-labs .legend");
  let box = $("#optDosesLabel"), key = $("#doseLegend");
  if (!DOSES.length) { box?.remove(); key?.remove(); dosesOnCharts = false; return; }
  if (!box && meds) meds.after(box = el("label", {id: "optDosesLabel", class: "toggle"}, el("input", {type: "checkbox", id: "optDoses", checked: dosesOnCharts || null, onchange: ev => {
    dosesOnCharts = ev.target.checked; try { localStorage.setItem("sg-doses-charts", dosesOnCharts ? "1" : "0"); } catch (e) {}
    $("#doseLegend").hidden = !dosesOnCharts; renderCharts(); }}), " Doses logged"));
  if (!key && legend) legend.append(key = el("span", {id: "doseLegend", class: "jlegend", hidden: !dosesOnCharts || null},
    el("svg:svg", {viewBox: "0 0 40 12", width: 40, height: 12, "aria-hidden": "true"}, doseShape("taken", 6, 6), doseShape("late", 20, 6), doseShape("skipped", 34, 6)),
    "Doses logged: ●\u00a0taken, ◆\u00a0taken\u00a0late, ○\u00a0skipped"));   // each mark kept with its words (#355)
}
