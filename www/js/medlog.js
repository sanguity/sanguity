/* Sanguity · the medication log (#235): doses the person says were taken, taken late or skipped, beside the records.
   The records (GET api/profiles/{pid}/data) list the doses typed in as DATA.doses = [{id, med (the medication's key),
   name, t (wall-clock YYYY-MM-DDTHH:MM), status, note, slot (the scheduled time it was logged against), amount, unit,
   by, at, changed, changed_by}] (server/med_log.py); the phone's (Apple Health's Medications, sent by Health Auto Export) come with the
   watch data as WATCH.med_doses = [[time with offset, name, "taken" | "skipped", dosage, units, app]], matched to a
   medication by the same key as the records' (medKeyOf, meds.js) and read only.
   Where they show: the medication's drawer (its log, a weekly count, Log a dose), today's doses (each dose its
   schedule gives today, #1095) on the Medications tab's Today (medications.js, #275), where the log lives, and on the
   Overview, marks on the medication's span in the Timeline's and the Medications tab's lane, and a row under the Labs
   charts when "Doses logged" is ticked (#192's "Show on charts"). A dose is shown by its
   shape as well as its words (taken ●, taken late ◆, skipped ○), never by colour alone.
   It keeps what the person says happened. Nothing here says a dose was due or should be taken: counts are of the
   doses logged ("12 of 14 logged doses taken"), or with a schedule of the doses it gave so far ("9 of 14 scheduled
   doses taken", #1095), plain counts. Owners and editors log, correct and remove typed doses.
   Plain script, loaded in order by index.html after meds.js; all files share one global scope. */
"use strict";
const DOSE_ST = [["taken", "Taken"], ["late", "Taken late"], ["skipped", "Skipped"]];
const DOSE_LABEL = Object.fromEntries(DOSE_ST);
let DOSES = [];   // typed and the phone's, oldest first: {id (typed) | null, key, name, t, ts, status, note, slot, amount, unit, phone, app, dose}

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
/* its amount as said: the phone's as it sent it, a typed one's "2 tablets" (meds.js mrAmt); "" with none */
const doseAmt = d => d.phone ? d.dose : mrAmt(d);
const doseSay = d => `${DOSE_LABEL[d.status]}, ${d.name}, ${doseWhen(d)}` + (doseAmt(d) ? `, ${doseAmt(d)}` : "");
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
/* ---- against a schedule (#1095, server/med_log.match and schedule_counts): each of a day's scheduled doses with the
   dose logged for it. One logged against a time takes the first of that time not yet taken (two at 8:00 are two); the
   phone's, which have none, the nearest within DOSE_WINDOW minutes; the rest are others ("Also") */
const DOSE_WINDOW = 120;
const hmMin = hm => +hm.slice(0, 2) * 60 + +hm.slice(3, 5);
const doseHM = d => { const z = zdate(d.ts); return `${String(z.getHours()).padStart(2, "0")}:${String(z.getMinutes()).padStart(2, "0")}`; };
function doseMatch(sched, ds) {
  const got = sched.map(() => null), other = [], later = [];
  for (const d of ds) {   // (oldest first)
    if (!d.slot) { later.push(d); continue; }
    const k = sched.findIndex((s, i) => s.at === d.slot && !got[i]);
    if (k < 0) other.push(d); else got[k] = d;
  }
  for (const d of later) {
    let k = -1;
    if (d.phone) {
      const hm = hmMin(doseHM(d)), gap = i => Math.abs(hmMin(sched[i].at) - hm);
      sched.forEach((s, i) => { if (!got[i] && gap(i) <= DOSE_WINDOW && (k < 0 || gap(i) < gap(k))) k = i; });
    }
    if (k < 0) other.push(d); else got[k] = d;
  }
  return {got, other};
}
/* {scheduled, taken (late ones too), late, skipped, other} of a medication's doses on the days ("YYYY-MM-DD"): each of
   its schedule's counted once its time has come or a dose is logged for it; other: those logged that aren't one */
function doseSched(m, days) {
  const n = {scheduled: 0, taken: 0, late: 0, skipped: 0, other: 0}, today = zToday(), hm = doseHM({ts: Date.now()}), mine = dosesOf(m);
  for (const day of days) {
    if (day > today) continue;
    const sched = mrDoses(m, day), {got, other} = doseMatch(sched, mine.filter(d => doseDay(d) === day));
    n.other += other.length;
    sched.forEach((s, i) => {
      const d = got[i];
      if (day === today && s.at > hm && !d) return;
      n.scheduled++;
      if (d) { n.taken += d.status !== "skipped"; n.late += d.status === "late"; n.skipped += d.status === "skipped"; }
    });
  }
  return n;
}
/* "9 of 14 scheduled doses taken (1 late), 2 skipped; 1 other dose logged" (server/med_log.said_scheduled) */
function doseSchedSaid(c) {
  return (!c.scheduled ? "No scheduled doses yet" : `${c.taken} of ${c.scheduled} scheduled dose${c.scheduled === 1 ? "" : "s"} taken` + (c.late ? ` (${c.late} late)` : "")
    + (c.skipped ? `, ${c.skipped} skipped` : "")) + (c.other ? `; ${c.other} other dose${c.other === 1 ? "" : "s"} logged` : "");
}
const weekDays = () => { const w = dayTs(weekOf(zToday())); return [...Array(7)].map((_, i) => isoDay(w + i * DAY)); };
/* this week's words for medications: of those with a schedule, the doses taken of those it gave so far, and how many
   others were logged; with none, what was logged ("12 of 14 logged doses taken") */
function doseWeekSaid(meds) {
  const timed = meds.filter(m => mrOf(m).some(r => !r.asneeded)), keys = new Set(timed.map(m => m.key));
  const rest = thisWeek(DOSES.filter(d => meds.some(m => m.key === d.key) && !keys.has(d.key)));
  if (!timed.length) return doseSaid(doseCounts(rest));
  const days = weekDays(), n = timed.map(m => doseSched(m, days)).reduce((a, c) => { for (const k in a) a[k] += c[k]; return a; });
  n.other += rest.length;
  return doseSchedSaid(n);
}

/* ---------------------------------------------------------------- the medication's drawer: its log */
function medLogSection(m) {
  const ds = dosesOf(m), write = canEdit(), cur = mrCurrent(m), timed = mrOf(m).some(r => !r.asneeded);
  if (!ds.length && !(write && cur) && !timed) return [];
  const out = [el("div", {class: "sect", text: "Doses logged"})];
  if (ds.length || timed) out.push(el("p", {class: "dose-week", text: "This week: " + doseWeekSaid([m])}));
  if (ds.length) {
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
        el("span", {class: "dose-says"}, el("b", {text: DOSE_LABEL[d.status]}), " · " + doseWhen(d) + (doseAmt(d) ? ` · ${doseAmt(d)}` : "")),
        el("span", {class: "dose-src", text: d.phone ? `From ${d.app}` : `Logged by ${d.by || "someone"}` + (d.slot ? ` · scheduled at ${mrTime(d.slot)}` : "")}),
        d.note ? el("span", {class: "dose-note", text: d.note}) : null,
        write && !d.phone ? el("span", {class: "mr-acts needs-server"},
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
  if (write && cur) out.push(el("div", {class: "btns"}, el("button", {class: "btn needs-server", type: "button", text: "Log a dose", onclick: () => doseForm(m, null)})));
  out.push(el("p", {class: "hint", text: "What was logged, as it was said: taken, taken late or skipped. A record kept for the person, not medical advice."}), msg);
  return out;
}

/* the log / correct form, in the drawer. d: the dose (null: a new one); preset: {status, slot, amount, unit} */
function doseForm(m, d, preset = {}) {
  const body = drawerHead("med", d ? "Correct a dose" : "Log a dose", m.name);
  drawerStill = () => MEDS.some(x => x.key === m.key);   // the medication's import undone: closed (app.js, #517)
  const uid = Math.random().toString(36).slice(2, 8), id = n => `dose-${n}-${uid}`;
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  let status = d ? d.status : preset.status || "taken";
  const seg = el("div", {class: "seg wrap dose-st", role: "radiogroup", "aria-label": "What happened"});
  const set = s => { status = s; for (const b of seg.children) { b.setAttribute("aria-checked", b.dataset.st === s); b.tabIndex = b.dataset.st === s ? 0 : -1; } };
  seg.append(...DOSE_ST.map(([s, t]) => el("button", {type: "button", role: "radio", "data-st": s, text: t, onclick: () => set(s),
    onkeydown: ev => { const i = DOSE_ST.findIndex(([x]) => x === status), k = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[ev.key];
      if (k) { ev.preventDefault(); const n = DOSE_ST[(i + k + DOSE_ST.length) % DOSE_ST.length][0]; set(n); seg.querySelector(`[data-st="${n}"]`).focus(); } }})));
  set(status);
  const now = zdate(Date.now()), hhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  const date = el("input", {type: "date", id: id("date"), required: true, max: zToday()}), time = el("input", {type: "time", id: id("time"), required: true});
  date.value = d ? d.t.slice(0, 10) : zToday(); time.value = d ? d.t.slice(11, 16) : hhmm;
  const note = el("textarea", {id: id("note"), rows: 3, maxlength: 1000}); note.value = d ? d.note || "" : "";
  // how much (#1095): the scheduled time's or the usual As needed amount to start with, or the dose's own
  const amt = el("input", {type: "text", id: id("amt"), inputmode: "decimal", size: 5, maxlength: 12, autocomplete: "off"}), unit = el("input", {type: "text", id: id("unit"), size: 9, maxlength: 40, autocomplete: "off"});
  const was = d || preset;
  amt.value = was.amount != null ? String(was.amount) : ""; unit.value = was.unit || "";
  const row = (lbl, input) => el("div", {class: "jfield"}, el("label", {for: input.id, text: lbl}), input);
  const form = el("form", {class: "jform", novalidate: true},
    el("div", {class: "jfield"}, el("span", {class: "jlabel", text: "What happened"}), seg),
    el("div", {class: "jwhen"}, row("Date", date), row("Time", time)), el("div", {class: "jwhen"}, row("Amount (optional)", amt), row("Unit", unit)),
    row("Note (optional)", note),
    el("div", {class: "btns"}, el("button", {class: "btn primary needs-server", type: "submit", text: d ? "Save" : "Log dose"}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => openMedication(m)})), msg);
  form.onsubmit = ev => {
    ev.preventDefault();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date.value) || !/^\d{2}:\d{2}/.test(time.value)) { sayIn(msg, "Give the date and time.", true); (date.value ? time : date).focus(); return; }
    const n = mrNum(amt.value);
    if (Number.isNaN(n)) { sayIn(msg, "The amount isn't a number above 0.", true); amt.setAttribute("aria-invalid", "true"); amt.addEventListener("input", () => amt.removeAttribute("aria-invalid"), {once: true}); amt.focus(); return; }
    const out = {med: m.key, t: `${date.value}T${time.value.slice(0, 5)}`, status, note: note.value, slot: d ? d.slot : preset.slot || null, amount: n, unit: unit.value.trim()};
    doseSend(d ? `/${d.id}` : "", {method: d ? "PUT" : "POST", body: JSON.stringify(out)}, msg, {med: m.key});
  };
  body.append(form);
  showDrawer();
  seg.querySelector("[aria-checked=true]").focus({preventScroll: true});
}
/* every change: the reply's dose put in the records in place (store.js, #271), which draws again what shows the
   doses (today's, the lanes, the charts), and the medication's drawer drawn again (after: {med}); logged from today's
   doses (after: {row, say}, its row's data-dose-row and what was logged, said, in the tab shown: the Medications tab's or the Overview's, #275), focus
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
  announce(opts.method === "DELETE" ? "Dose removed from the log." : after.say ? `Logged. ${after.say}.` : "Dose logged.");
}

/* ---------------------------------------------------------------- today's doses: the Medications tab's and the Overview's
   For each current medication: a row for each dose its schedule gives today (#1095, meds.js mrDoses: every line whose
   days have today, between from and its end), two at one time two, each with its amount, logged one by one; a row
   As needed when its schedule has that line (Log a dose, with its usual amount); else one row when it has no schedule
   ("Any time"). Each logged dose of today beside the scheduled one it is (doseMatch: logged against its time, or the
   phone's within DOSE_WINDOW of it), the rest in an "Also" row after the medication's last (in its As needed row, with
   one). The rows in order of their times, those with none last. Owners and editors log with one click (the time is
   now, the amount the schedule's); viewers see what was logged, or that nothing was. None for a profile without
   current medications. A card: id and head, its own (the Medications tab's Today, #275: medications.js), else the
   Overview's.
   few (the Overview's, #440, so Upcoming stays on a phone's first screen): each row with nothing logged one line, Taken
   its one button and Taken late and Skipped under its ⋯ (one with doses logged wraps them under its name, as the
   Medications tab's do: .logged, #468); and only that many rows, from the latest scheduled time that has come (the
   first while none has), the number left out in the card's data-more (the Overview links to the rest) */
function todayDoses({id = "ovDoses", head = el("h3", {text: "Today's medication doses"}), few = 0} = {}) {
  const write = canEdit(), today = zToday();
  const cur = MEDS.filter(mrCurrent);
  const todays = DOSES.filter(d => doseDay(d) === today);
  if (!cur.length) return null;
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  // offline (offline.js: the kept copy, read-only) the buttons stay, each saying why it does nothing, as Add's Log a dose does
  const away = typeof OFFLINE !== "undefined" && OFFLINE.on, off = away ? {"aria-disabled": "true", "data-tip": "Needs the server: you're offline"} : {};
  const now = () => `${today}T${doseHM({ts: Date.now()})}`;
  // s: the scheduled dose ({at, amount, unit}) or null; row: its row's data-dose-row, where focus goes once it's logged
  const log = (m, status, s, row, what) => away || doseSend("", {method: "POST", body: JSON.stringify({med: m.key, t: now(), status, slot: s ? s.at : null, note: "",
    amount: s ? s.amount ?? null : null, unit: s ? s.unit || "" : ""})}, msg, {row, say: `${DOSE_LABEL[status]}: ${what}`});
  const btn = (st, t, m, s, row, what) => el("button", {class: "btn small", type: "button", text: t, "aria-label": `${t}: ${what}`, ...off, onclick: () => log(m, st, s, row, what)});
  const quick = (m, s, row, what) => el("span", {class: "dose-quick", role: "group", "aria-label": `Log ${what}`},
    ...(few ? [btn("taken", "Taken", m, s, row, what), doseMore(what, off, st => log(m, st, s, row, what))] : DOSE_ST.map(([st, t]) => btn(st, t, m, s, row, what))));
  const logged = d => el("span", {class: "dose-done"}, doseIcon(d.status), [DOSE_LABEL[d.status], fmtTime(d.ts), doseAmt(d)].filter(Boolean).join(" · ") + (d.phone ? ` · from ${d.app}` : ""));
  const none = () => el("span", {class: "hint", text: "Nothing logged"});
  const rows = [];   // [its time, its row]
  for (const m of cur) {
    const sched = mrDoses(m, today), need = mrAsNeeded(m, today), {got, other} = doseMatch(sched, todays.filter(d => d.key === m.key));
    const open = () => el("button", {class: "cell-btn dose-med", type: "button", text: m.name, "data-tip": "Its details and log", onclick: () => openMedication(m)});
    const nth = {};
    sched.forEach((s, k) => {
      const n = nth[s.at] = (nth[s.at] || 0) + 1, row = m.key + " " + s.at + (n > 1 ? " " + n : ""), amt = mrAmt(s), d = got[k];
      const what = `${m.name}, ${amt ? amt + " at " : ""}${mrTime(s.at)}`;
      rows.push([s.at, el("li", {class: "dose-today" + (d ? " logged" : ""), "data-dose-row": row},
        el("span", {class: "dose-slot"}, mrTime(s.at), ...(amt ? [el("br"), amt] : [])), open(),
        d ? el("span", {class: "dose-got"}, logged(d)) : write ? quick(m, s, row, what) : none())]);
    });
    const last = (sched.at(-1)?.at || "") + "~";
    if (need) {   // As needed (#1095): Log a dose, its usual amount to start with; what was logged beside it
      const amt = mrAmt(need.dose);
      rows.push([last, el("li", {class: "dose-today" + (other.length ? " logged" : ""), "data-dose-row": m.key + " "},
        el("span", {class: "dose-slot"}, "As needed", ...(amt ? [el("br"), amt] : [])), open(),
        write || other.length ? el("span", {class: "dose-got"}, ...other.map(logged), write ? el("button", {class: "btn small", type: "button", text: "Log a dose",
          "aria-label": `Log a dose: ${m.name}, as needed`, ...off, onclick: () => away || doseForm(m, null, {amount: need.dose?.amount, unit: need.dose?.unit})}) : null) : none())]);
    } else if (!sched.length || other.length) rows.push([last, el("li", {class: "dose-today" + (other.length ? " logged" : ""), "data-dose-row": m.key + " "},
      el("span", {class: "dose-slot", text: sched.length ? "Also" : "Any time"}), open(),
      write || other.length ? el("span", {class: "dose-got"}, ...other.map(logged), write ? quick(m, null, m.key + " ", m.name) : null) : none())]);
  }
  if (!write && !todays.length) rows.length = 0;   // a viewer's, with nothing logged: the hint says so
  // by time ("HH:MM"), its Also after its medication's last ("HH:MM~"), those with none last ("~"); stable, so a
  // medication's own keep their order
  rows.sort((a, b) => (a[0] > b[0]) - (a[0] < b[0]));
  let from = 0, n = rows.length;
  if (few && rows.length > few) {
    const hm = now().slice(11), came = rows.filter(r => r[0].length === 5 && r[0] <= hm).at(-1);
    from = Math.min(came ? rows.findIndex(r => r[0] === came[0]) : 0, rows.length - few); n = few;
  }
  return el("div", {class: "card", id, "data-more": rows.length - n || null}, head,
    el("p", {class: "hint", text: !write ? (todays.length ? "What was logged today." : "Nothing logged today.") : away ? "Logging a dose needs the server: you're offline."
      : "Log what happened, for your own record: taken, taken late or skipped."}),
    rows.length ? el("ul", {class: "doselist today" + (few ? " few" : "")}, ...rows.slice(from, from + n).map(r => r[1])) : null,
    el("p", {class: "dose-week", text: "This week: " + doseWeekSaid(cur)}),
    aboutWords({name: "today's medication doses"}, `The times and amounts are those of the schedule set on each medication (its details, ${id === "ovDoses" ? "on the Medications tab" : "below"}). This keeps what is logged, nothing more. Doses logged in Apple Health's Medications come with the phone's data when Health Auto Export sends them, each beside the scheduled dose within two hours of it. This isn't medical advice.`),   // (#447: on the Medications tab, they're below it; one name on both, #460)
    msg);
}

/* the Overview's ⋯ beside Taken (#440): Taken late and Skipped in a menu (nav.js SgNav.menu, as Settings' row menus);
   offline (off), the button alone, saying why. log(status) logs it */
let doseMenus = 0;
function doseMore(what, off, log) {
  const id = "doseMore" + (++doseMenus), live = !off["aria-disabled"];
  const btn = el("button", {class: "btn small dose-more", type: "button", "aria-label": `Taken late or skipped: ${what}`, "data-tip": "Taken late or skipped", ...off,
    ...(live ? {"aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": id} : {})}, icon("ellipsis"));
  if (!live) return btn;
  const list = el("div", {class: "menu-list", id, role: "menu", "aria-label": `Log ${what}`}, ...DOSE_ST.slice(1).map(([s, t]) =>
    el("button", {class: "menu-item", role: "menuitem", type: "button", tabindex: "-1", text: t, onclick: () => log(s)})));
  return SgNav.menu({btn, list, wrap: el("span", {class: "menu"}, btn, list)}).wrap;
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

/* ---------------------------------------------------------------- Labs: one row of doses on the event track, when ticked */
let dosesOnCharts = false;
try { dosesOnCharts = localStorage.getItem("sg-doses-charts") === "1"; } catch (e) {}
const dosesInWindow = () => dosesOnCharts && state.cats.has("med") ? DOSES.filter(d => d.ts >= state.from && d.ts <= state.to) : [];
CHART_LAYERS.push({
  name: "Doses logged", short: "Doses",
  space: () => dosesInWindow().length ? 18 * ckK() : 0,
  rows: () => dosesInWindow().map(d => { const m = MEDS.find(m => m.key === d.key); return [d.ts, "Dose logged", doseSay(d), m && (() => openMedication(m)), "med"]; }),
  draw(g, x, top, R, L) {
    const y = top + 9 * ckK();
    for (const d of dosesInWindow()) {
      const dx = x(d.ts); if (dx < L - 2 || dx > R + 3) continue;
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
