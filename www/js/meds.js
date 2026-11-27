/* Sanguity · the medication timeline (#50): each medication as spans, with its dose changes and pharmacy fills.
   The records (GET api/profiles/{pid}/data) list them as DATA.medications = [{name, key, rxnorm, spans: [{from, to,
   open, stopped, dose, title, status, prescriber, sources, ids, rxnorm}], changes: [{t, from, to, id}], fills: [{t,
   id, title, pharmacy, supply}], start, stop}] (server/medications.py works them out from the med and rx events).
   On the Timeline the Medications lane draws one row per medication: a bar for each span (a new shade where the
   dose changes), a cap where the records say it stopped, an arrow where they give no end, and a tick for each fill.
   On Labs the same rows can be shown once, on the event track over the charts (#294; the "Medications" tick box, off until ticked).
   It shows what the records say, never whether a dose or a gap is right. Every date goes through dates.js.
   Its schedule (#1094, with #162's reminders, server/med_reminders.py): DATA.reminders = [{id, med (the medication's
   key), name, times, amounts, days, from, until, tz, asneeded, dose, on (Remind me), by, at}], its lines, set in a
   medication's drawer by owners and editors for a current medication; those with Remind me on listed by the calendar
   feed (Sharing → Calendar feed) as repeating events; the drawer says when one has ended.
   The medication log (#235, medlog.js) adds its doses to the drawer (medLogSection) and as marks on each span in the
   Timeline's lane (medDoseMarks).
   Plain script, loaded in order by index.html after journal.js; all files share one global scope. */
"use strict";
let MEDS = [];   // the medications
/* the medication whose history the drawer shows (openMedication marks the drawer with its key; drawerHead, drawing
   anything else there, takes the mark away), or null: not a listing or a fill of it, which have its name (#417) */
const medShown = () => drawer.classList.contains("open") && drawer.dataset.med != null && MEDS.find(m => m.key === drawer.dataset.med) || null;

function medsLoad() {
  MEDS = (Array.isArray(DATA.medications) ? DATA.medications : []).filter(m => m && Array.isArray(m.spans) && Array.isArray(m.fills)).map((m, i) => {
    const spans = m.spans.filter(s => s && /^\d{4}-\d{2}-\d{2}$/.test(s.from || "")).map(s => ({...s, ts: dayTs(s.from), te: s.to ? dayTs(s.to) : null}));
    const fills = m.fills.filter(f => f && /^\d{4}-\d{2}-\d{2}$/.test(f.t || "")).map(f => ({...f, ts: byId.get(f.id)?.ts ?? dayTs(f.t)}));
    const words = [m.name, ...spans.flatMap(s => [s.title, s.dose, s.prescriber]), ...fills.map(f => f.title), ...(m.rxnorm || []), "medication"];
    // (#1092) what the records list of one you changed, its spans as they were
    const listed = m.listed && Array.isArray(m.listed.spans) ? {...m.listed, spans: m.listed.spans.filter(s => s && /^\d{4}-\d{2}-\d{2}$/.test(s.from || "")).map(s => ({...s, ts: dayTs(s.from), te: s.to ? dayTs(s.to) : null}))} : null;
    return {...m, i, spans, fills, listed, changes: Array.isArray(m.changes) ? m.changes : [], _q: words.filter(Boolean).join(" ").toLowerCase()};
  });
}
/* where a span ends on screen: its last day, or today when the records give no end */
const medEnd = s => s.te ?? Math.max(s.ts, Date.now());
const medFrom = m => Math.min(...m.spans.map(s => s.ts), ...m.fills.map(f => f.ts));
const medTo = m => Math.max(...m.spans.map(medEnd), ...m.fills.map(f => f.ts + (f.supply || 0) * DAY));
/* q: the Timeline's search; the Labs charts show every medication, whatever was searched there (#192) */
const medsShown = (a, b, q = state.q) => MEDS.filter(m => (m.spans.length || m.fills.length) && medTo(m) >= a && medFrom(m) <= b && (!q || m._q.includes(q)));
/* "5 Jan 2026 – 3 Mar 2026", "5 Jan 2026 – no end in the records", "stopped 5 Jan 2026 (no start or end date)" */
function medSpanWhen(s) {
  if (s.open) return `${fmtD(s.ts)} – no end in the records`;
  if (s.stopped && s.to === s.from) return `${fmtD(s.ts)} · listed as ${(s.status || "stopped").toLowerCase()}, no end date`;
  return `${fmtD(s.ts)} – ${fmtD(s.te)}`;
}
const medSpanSays = s => [s.title, s.dose].filter(Boolean).join(" · ");

/* ---------------------------------------------------------------- one row per medication, drawn by kind of mark */
const MED_ROW = 18;
function medRow(g, m, x, y, L, R, color, opts = {}) {
  const sel = opts.selectable && medShown() === m;
  m.spans.forEach((s, n) => {
    const x1 = x(s.ts), x2 = x(medEnd(s));
    if (x2 < L || x1 > R) return;
    const a = Math.max(L, x1), b = Math.min(R, Math.max(x2, x1 + 3));
    const changed = n > 0 && m.changes.some(c => c.t === s.from);
    const attrs = {class: "medbar" + (opts.selectable ? " ev" : "") + (sel ? " sel" : ""), x: a, y: y - 4, width: Math.max(3, b - a), height: 8, rx: 2,
      fill: color, "fill-opacity": n % 2 ? .55 : .85, "data-med": m.i, "data-span": n};
    g.push(el("svg:rect", attrs));
    if (changed && x1 >= L && x1 <= R) g.push(el("svg:line", {class: "medchange", x1, x2: x1, y1: y - 7, y2: y + 7, "pointer-events": "none"}));
    if (s.stopped && x2 >= L && x2 <= R) g.push(el("svg:line", {class: "medstop", x1: b, x2: b, y1: y - 6, y2: y + 6, "pointer-events": "none"}));
    if (s.open && x2 <= R + 1) g.push(el("svg:path", {class: "medopen", d: `M${b + 1},${y - 4}L${b + 6},${y}L${b + 1},${y + 4}Z`, fill: color, "fill-opacity": .6, "pointer-events": "none"}));
  });
  if (opts.selectable) medDoseMarks(g, m, x, y, L, R);   // doses logged, on the span (medlog.js, #235)
  for (const f of m.fills) {
    const fx = x(f.ts); if (fx < L - 2 || fx > R + 2) continue;
    if (f.supply) { const fe = Math.min(R, x(f.ts + f.supply * DAY)); if (fe > fx + 1) g.push(el("svg:line", {class: "medsupply", x1: Math.max(L, fx), x2: fe, y1: y + 7, y2: y + 7, "pointer-events": "none"})); }
    g.push(el("svg:path", {class: "medfill" + (opts.selectable ? " ev" : "") + (state.selEv === f.id ? " sel" : ""), d: `M${fx},${y + 3}L${fx + 3.5},${y + 9}L${fx - 3.5},${y + 9}Z`,
      "data-id": opts.selectable ? f.id : null, "data-med": m.i, "data-fill": f.id}));
  }
}
/* a medication's name whole (#643: cut to "Acyclo…" with larger text, its title no help on a touch screen): broken
   into lines no wider than room, each word whole; null where a word is wider than room. Drawn as one label, a line
   MED_LH px under another (ckK), the first's baseline at y; cut only where a word is wider than a whole lane */
const MED_LH = 13;
/* a row's height where its name, of n lines, is over its bars: its bar under the name's last line, clear of its letters'
   tails with larger text (#1074: drawn over the bar at 200%) */
const medOverH = (n, k) => (19 + (n - 1) * MED_LH) * k + 7;
const medLines = (name, room) => name.split(" ").every(w => labelW(w, "medname") <= room) ? ckLines(name, "medname", room) : null;
function medName(m, lines, x, y, k) {
  return el("svg:text", {class: "medname", x, y}, lines.length > 1 ? lines.map((l, i) => el("svg:tspan", {x, dy: i ? MED_LH * k : null, text: l})) : lines[0],
    lines.join(" ") !== m.name ? el("svg:title", {text: m.name}) : null);
}
/* the Timeline's Medications lane (timeline.js asks TL_LANES for its height and drawing): a row a medication, its name in
   the gutter under the lane's, broken into lines that fit it; or over its bars, broken to the plot's width, where the
   lanes' names are over them, or a word of one doesn't fit the gutter (larger text, #633, #643). Each row as tall as
   its name's lines */
function medLaneRows(S) {
  const ms = medsShown(state.from, state.to), over = S.over || !ms.every(m => medLines(m.name, S.L - 14)), k = S.k;
  return {over, rows: ms.map(m => {
    const lines = medLines(m.name, over ? S.R - S.L : S.L - 14) || [ckFit(m.name, "medname", S.R - S.L)];
    return {m, lines, h: over ? medOverH(lines.length, k) : Math.max(MED_ROW, lines.length * MED_LH + 5) * k};
  })};
}
TL_LANES.med = {
  shows: () => medsShown(state.from, state.to).length > 0,
  labelY: S => 11 * S.k,
  height(w, S) {
    if (!MEDS.length) return 0;
    const rows = medLaneRows(S).rows;
    return (S.over ? 0 : 20 * S.k) + (rows.length ? rows.reduce((a, r) => a + r.h, 0) : S.over ? 12 * S.k + 14 : MED_ROW * S.k);
  },
  draw(g, S, top, h, c) {
    const {L, R, x, k} = S, {over, rows} = medLaneRows(S);
    let y0 = top + (S.over ? 0 : 20 * k);
    for (const {m, lines, h: rh} of rows) {
      const y = over ? y0 + rh - 7 : y0 + rh / 2, wide = Math.max(...lines.map(l => labelW(l, "medname")));
      g.push(medName(m, lines, over ? Math.max(L, Math.min(x(medFrom(m)), R - wide)) : 8, over ? y0 + 11 * k : y + 4 * k - (lines.length - 1) * MED_LH * k / 2, k));
      medRow(g, m, x, y, L, R, c.color, {selectable: true});
      y0 += rh;
    }
  },
};
function medTip(m, ds, ev) {
  if (!m) return hideTip();
  if (ds.fill) { const e = byId.get(+ds.fill); if (e) return showEvTip(e, ev); }
  if (ds.dose != null) return doseTip(m, +ds.dose, ev);   // a dose logged (medlog.js, #235)
  const s = m.spans[+ds.span]; if (!s) return hideTip();
  showTip([el("div", {class: "v", text: m.name}),
    el("div", {class: "s"}, el("span", {class: "k", style: {background: CAT_OF.med.color}}), medSpanWhen(s)),
    medSpanSays(s) && medSpanSays(s) !== m.name ? el("div", {class: "s", text: medSpanSays(s)}) : null,
    s.status ? el("div", {class: "s", text: "Status: " + s.status}) : null,
    s.prescriber ? el("div", {class: "s", text: "Prescribed by " + s.prescriber}) : null,
    m.fills.length ? el("div", {class: "s", text: `${m.fills.length} pharmacy fill${m.fills.length > 1 ? "s" : ""} in the records`}) : null], ev);
}

/* ---------------------------------------------------------------- Labs: the same rows on the event track, when ticked */
/* once, over the charts (event-track.js, #294): each medication's name over its spans, clear of the fills of the row
   above (MED_TRACK px a row), whole: broken into lines no wider than the plot from L to R (medLines, as the Timeline's,
   #643), its row as tall as them (#978: cut to the plot and to 40 letters) */
let medsOnTrends = false;
try { medsOnTrends = localStorage.getItem("sg-meds-trends") === "1"; } catch (e) {}
const medsInWindow = () => medsOnTrends && state.cats.has("med") ? medsShown(state.from, state.to, "") : [];
const MED_TRACK = 30;
const medTrackRows = (L, R) => medsInWindow().map(m => {
  const lines = medLines(m.name, R - L - 2) || [ckFit(m.name, "medname", R - L - 2)];
  return {m, lines, h: (MED_TRACK + (lines.length - 1) * MED_LH) * ckK()};
});
CHART_LAYERS.push({
  name: "Medications",
  space: (L = 0, R = Infinity) => { const rows = medTrackRows(L, R); return rows.length ? 4 + rows.reduce((a, r) => a + r.h, 0) : 0; },
  rows: () => medsInWindow().flatMap(m => m.spans.filter(s => medEnd(s) >= state.from && s.ts <= state.to).map(s => [s.ts, "Medication", `${m.name} · ${medSpanWhen(s)}`, () => openMedication(m), "med"])),
  draw(g, x, top, R, L) {   // each name over its row's spans, as tall as the text (#633)
    const k = ckK();
    let y0 = top + 4;
    for (const {m, lines, h} of medTrackRows(L, R)) {
      const y = y0 + h - 10, wide = Math.max(...lines.map(l => labelW(l, "medname")));
      g.push(medName(m, lines, Math.max(L + 2, Math.min(x(medFrom(m)), R - wide)), y - 8 - (lines.length - 1) * MED_LH * k, k));
      y0 += h;
      const before = g.length;
      medRow(g, m, x, y, L, R, CAT_OF.med.color);
      for (const node of g.slice(before)) if (node.dataset?.med != null) {
        node.addEventListener("pointermove", ev => { ev.stopPropagation(); medTip(m, node.dataset, ev); });
        node.addEventListener("pointerleave", hideTip);
        node.addEventListener("click", ev => { ev.stopPropagation(); hideTip(); node.dataset.fill ? openEvent(byId.get(+node.dataset.fill)) : openMedication(m); });
        node.style.cursor = "pointer";
      }
    }
  },
});
{
  const marks = $("#optMarks")?.closest("label");
  if (marks) marks.after(el("label", {id: "optMedsLabel", class: "toggle"}, el("input", {type: "checkbox", id: "optMeds", checked: medsOnTrends || null, onchange: ev => {
    medsOnTrends = ev.target.checked; try { localStorage.setItem("sg-meds-trends", medsOnTrends ? "1" : "0"); } catch (e) {} renderCharts(); }}), " Medications"));
}

/* ---------------------------------------------------------------- the drawer: one medication's history */
function openMedication(m) {
  if (!m) return;
  state.selEv = null;
  const last = m.spans[m.spans.length - 1];
  const body = drawerHead("med", m.name, (m.own ? "Your own entry · " : "Medication history · ") + (m.spans.length ? `${fmtD(m.spans[0].ts)} – ${last.open ? (m.own ? "no end date" : "no end in the records") : fmtD(last.te)}` : `pharmacy fills only, from ${fmtD(m.fills[0].ts)}`));   // drawer.js (#270)
  drawer.dataset.med = m.key;   // (medShown)
  // its key facts, as every record's (#406): what the head says (when it started, that the records give no end) isn't
  // said again
  body.append(...present(drawerFacts([["Started", m.spans.length ? fmtD(m.spans[0].ts) : ""], ["Stopped", m.stop ? fmtD(dayTs(m.stop)) : ""],
    ["Dose changes", m.changes.length ? String(m.changes.length) : ""], ["Pharmacy fills", m.fills.length ? String(m.fills.length) : ""],
    ["RxNorm", (m.rxnorm || []).join(", ")]], "facts")));
  body.append(...medMineSection(m));   // your own entry, or your change beside the records', and Edit (medmine.js, #1092)
  const listed = m.listed ? m.listed.spans : m.own ? [] : m.spans;   // what the records list (none: your own entry)
  if (listed.length) {
    body.append(el("div", {class: "sect", text: "What the records list"}), scrollBox({class: "tablewrap", "aria-label": "What the records list", style: {maxHeight: "none"}}, el("table", {class: "data medspans"},
      el("thead", {}, el("tr", {}, ["When", "Listed as", "Status", "Prescribed by", "Source"].map(h => el("th", {text: h})))),
      el("tbody", {}, listed.map(s => el("tr", {class: "link", title: "Open this listing", onclick: () => openEvent(byId.get(s.ids[0]))},
        el("td", {text: medSpanWhen(s)}), el("td", {}, el("button", {class: "cell-btn", type: "button", text: medSpanSays(s)})), el("td", {text: s.status || ""}), el("td", {text: s.prescriber || ""}),
        el("td", {text: (s.sources.length ? s.sources : [...new Set(s.ids.map(id => importName(byId.get(id)?.imp)).filter(Boolean))]).join(", ")})))))));
  }
  if (m.changes.length) body.append(el("div", {class: "sect", text: "Changes"}), el("ul", {class: "medchanges"},
    ...m.changes.map(c => el("li", {}, el("b", {text: fmtD(dayTs(c.t))}), ` · ${c.from} → ${c.to}`))));
  if (m.fills.length) {
    body.append(el("div", {class: "sect", text: "Pharmacy fills"}), scrollBox({class: "tablewrap", "aria-label": "Pharmacy fills", style: {maxHeight: "none"}}, el("table", {class: "data medfills"},
      el("thead", {}, el("tr", {}, ["Date", "Drug", "Pharmacy", "Supply"].map(h => el("th", {text: h})))),
      el("tbody", {}, m.fills.map(f => el("tr", {class: "link", title: "Open this fill", onclick: () => openEvent(byId.get(f.id))},
        el("td", {text: fmtD(f.ts)}), el("td", {}, el("button", {class: "cell-btn", type: "button", text: f.title})), el("td", {text: f.pharmacy || ""}), el("td", {text: f.supply ? `${f.supply} days` : ""})))))));
  }
  body.append(...medReminderSection(m));   // its schedule, and its reminders through the calendar feed (#1094, #162)
  body.append(...medLogSection(m));        // the doses logged (medlog.js, #235)
  const notes = JOURNAL.filter(e => e.j.med && (m.own ? e.j.med === m.name : medKeyOf(e.j.med) === m.key));   // your own: by its name (#1091)
  if (notes.length) body.append(el("div", {class: "sect", text: "Journal entries linked to it"}),
    ...notes.sort((a, b) => a.ts - b.ts).map(e => el("button", {class: "btn", type: "button", text: `${fmtD(e.ts)} · ${e.title}`, onclick: () => openEvent(e)})));
  // your own entry is what you wrote, not a listing of the records (#1092's check: it said both of the records)
  body.append(el("p", {class: "hint", style: {color: "var(--ink2)"}, text: m.own ? "Your own entry, as you wrote it. This isn't medical advice."
      : "What the records say, in order: a listing's dates may not be when a medication started or ended. This isn't medical advice."}),
    ...(m.own ? [] : [aboutWords("A clinic's medication list dates a medication from when it was recorded there, which may not be when it was started; a listing with no end date may still have ended.")]),
    el("div", {class: "btns"}, el("button", {class: "btn", type: "button", text: "Zoom timeline to this medication", onclick: () => {
      const a = medFrom(m), b = medTo(m), pad = Math.max(7 * DAY, (b - a) * .05); setRange(a - pad, b + pad); pickTab("timeline"); }})));
  showDrawer();
  renderTimeline();
}
/* the medication's history drawn again after a change: when the drawer shows it already, in place, as it was (its
   scroll, its log shown whole, focus: drawer.js drawerKeep, #404) */
function medAgain(m) {
  if (m && medShown() === m) return drawerKeep(() => openMedication(m));
  openMedication(m);
}
// a dose logged elsewhere (the Overview's, beside the panel) while the panel shows this medication: drawn again (#271)
// (focus stays where it was, outside the panel)
storeOn("medications", () => { const m = medShown(); if (m) medAgain(m); });
/* the same grouping as server/medications.med_key: the leading words before any strength or form */
function medKeyOf(name) {
  name = String(name || "").split(/\s+/).join(" ").trim();
  const m = /[\d(\[]|\b(?:tab|tabs|tablet|tablets|cap|caps|capsule|capsules|oral|solution|injection|suspension|cream|ointment|patch|inhaler|er|xr|sr|dr|hcl|hydrochloride|sodium|calcium|potassium|mg|mcg|ml)\b/i.exec(name);
  const head = (m ? name.slice(0, m.index) : name).replace(/^[\s,;:\-/]+|[\s,;:\-/]+$/g, "").toLowerCase();
  return (head.match(/[a-z][a-z'-]*/g) || []).join(" ") || name.toLowerCase();
}
/* in a medication listing's or fill's drawer: its whole history */
function medHistoryAction(e) {
  if (!e || (e.k !== "med" && e.k !== "rx")) return null;
  const m = MEDS.find(m => m.spans.some(s => s.ids.includes(e.id)) || m.fills.some(f => f.id === e.id));
  return m ? el("div", {class: "btns"}, el("button", {class: "btn", type: "button", text: "Medication history", onclick: () => openMedication(m)})) : null;
}


/* ---------------------------------------------------------------- a medication's schedule, and its reminders (#1094, #162)
   Set here, for a current medication (the records give no end for its latest listing): one or more lines, each its
   times of day with the amount at each (two at one time, two doses), its days, from and until; or As needed, with no
   times. Every day at 8:00 and Mondays and Fridays at 20:00 is two lines (the owner, on #1094). Today lists each dose
   a line gives that day (medlog.js todayDoses), and the weeks count them. With Remind me on, a line's times reach the
   person's calendar app through the calendar feed, with the amounts. A line ends on its until day, or sooner on the
   day the records say the medication stopped, and the list here says which. Owners and editors set, change, turn
   Remind me on or off and remove them; every change is put in the records in place (store.js, #271) and this
   medication's drawer is drawn again. DATA.reminders keeps the lines (server/med_reminders.py), "on": Remind me;
   "paused": kept with its times to resume (the owner, on #1090), its doses on no Today and in no count or calendar:
   mrAll lists every line (the drawer's), mrOf those not paused (what gives doses). */
const MR_DAYS = [["mo", "Mon", "Monday"], ["tu", "Tue", "Tuesday"], ["we", "Wed", "Wednesday"], ["th", "Thu", "Thursday"],
  ["fr", "Fri", "Friday"], ["sa", "Sat", "Saturday"], ["su", "Sun", "Sunday"]];
const MR_MAX = 24;   // times a day on one line (server/med_reminders.TIMES_MAX)
const mrAll = m => (Array.isArray(DATA.reminders) ? DATA.reminders : []).filter(r => r && r.med === m.key);
const mrOf = m => mrAll(m).filter(r => !r.paused);
const mrCurrent = m => !!m.spans.length && !!m.spans[m.spans.length - 1].open;
/* "8:00 AM" in this person's language, for a wall-clock "HH:MM" (no zone: it is the line's own) */
const mrTime = t => { const [h, n] = t.split(":").map(Number); return _fmt(Date.UTC(2000, 0, 1, h, n), {hour: "numeric", minute: "2-digit", timeZone: "UTC"}); };
const mrAnd = xs => xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs.at(-1);
/* an amount as said: "2 tablets", "0.5 ml"; "" with none (server/med_reminders.said) */
const mrAmt = a => a && a.amount != null ? `${String(+a.amount)} ${a.unit || ""}`.trim() : "";
function mrDays(days) {
  const k = MR_DAYS.map(d => d[0]).filter(d => days.includes(d)).join(",");
  return k === "mo,tu,we,th,fr,sa,su" ? "every day" : k === "mo,tu,we,th,fr" ? "on weekdays" : k === "sa,su" ? "at weekends"
    : "on " + mrAnd(MR_DAYS.filter(d => days.includes(d[0])).map(d => d[1]));
}
/* its end and why: {day, why: "stopped" | "until"} or null (server/med_reminders.ends) */
function mrEnds(r, m) {
  if (m.stop && (!r.until || m.stop < r.until)) return {day: m.stop, why: "stopped"};
  return r.until ? {day: r.until, why: "until"} : null;
}
/* "2 tablets at 8:00 AM and 1 tablet at 8:00 PM every day"; "8:00 AM on Mon and Fri" with no amounts; "As needed,
   usually 2 puffs" */
const mrWhat = r => r.asneeded ? "As needed" + (mrAmt(r.dose) ? `, usually ${mrAmt(r.dose)}` : "") + (r.days.length < 7 ? " " + mrDays(r.days) : "")
  : `${mrAnd([...new Set(r.times)].map(t => (a => a ? `${a} at ${mrTime(t)}` : mrTime(t))(mrTogether(r.times.flatMap((x, i) => x === t ? [mrAmt((r.amounts || [])[i])] : [])))))} ${mrDays(r.days)}`;
/* the doses at one time (server/med_reminders.together): "1 tablet and 5 ml"; two alike "2 doses of 1 tablet" */
const mrTogether = as => as.length > 1 && as.every(a => a === as[0]) ? `${as.length} doses` + (as[0] ? ` of ${as[0]}` : "") : as.filter(Boolean).join(" and ");
/* "… every day, from 18 Nov 2026, no end date" */
const mrSays = r => `${mrWhat(r)}, from ${fmtD(dayTs(r.from))}` + (r.until ? ` until ${fmtD(dayTs(r.until))}` : ", no end date");
function mrState(r, m) {
  const end = mrEnds(r, m);
  if (end && end.why === "stopped") return {cls: "ended", text: `Ended ${fmtD(dayTs(end.day))}: the records say this medication stopped then`};
  if (end && end.day < zToday()) return {cls: "ended", text: `Ended ${fmtD(dayTs(end.day))}`};
  if (r.paused) return {cls: "paused", text: "Paused: not on Today, in the counts or the calendar feed until resumed"};
  if (r.asneeded) return {cls: "asneeded", text: "Logged when taken: no reminders"};
  if (!r.on) return {cls: "quiet", text: "Remind me off: not in the calendar feed"};
  return {cls: "remind", text: "Remind me on: in the calendar feed"};
}
/* the doses its schedule gives on a day ("YYYY-MM-DD"), by time: [{at, amount, unit}], two at one time two (medlog.js) */
function mrDoses(m, day) {
  const k = MR_DAYS[(new Date(day + "T12:00:00Z").getUTCDay() + 6) % 7][0];
  return mrOf(m).filter(r => !r.asneeded && r.days.includes(k) && r.from <= day && !(mrEnds(r, m) && mrEnds(r, m).day < day))
    .flatMap(r => r.times.map((at, i) => ({at, amount: null, unit: "", ...(r.amounts || [])[i]})))
    .sort((a, b) => (a.at > b.at) - (a.at < b.at));
}
/* its As needed line that day, or null */
const mrAsNeeded = (m, day) => mrOf(m).find(r => r.asneeded && r.from <= day && !(mrEnds(r, m) && mrEnds(r, m).day < day)) || null;
function medReminderSection(m) {
  const rs = mrAll(m), write = canEdit(), cur = mrCurrent(m);
  if (!rs.length && !(write && cur)) return [];
  const out = [el("div", {class: "sect", text: "Schedule"})];
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  if (rs.length) out.push(el("ul", {class: "mr-list"}, ...rs.map(r => {
    const st = mrState(r, m), zone = r.tz && r.tz !== (DATES.zone || browserZone()) ? ` (${r.tz} time)` : "";
    return el("li", {class: "mr-item " + st.cls},
      el("span", {class: "mr-says", text: mrSays(r) + zone}),
      el("span", {class: "mr-state", text: st.text}),
      write ? el("span", {class: "mr-acts needs-server"},   // what saves, off while offline (offline.js, #449)
        st.cls !== "ended" || mrEnds(r, m)?.why === "until" ? el("button", {class: "btn small", type: "button", text: "Edit",
          "aria-label": "Edit the schedule line " + mrSays(r), onclick: () => medReminderForm(m, r)}) : null,
        st.cls !== "ended" && !r.asneeded && !r.paused ? el("button", {class: "toggle", type: "button", text: "Remind me", "aria-pressed": !!r.on,
          "aria-label": "Remind me: " + mrSays(r),
          onclick: () => mrSend(`/${r.id}`, {method: "PATCH", body: JSON.stringify({on: !r.on})}, msg, m)}) : null,
        st.cls !== "ended" ? el("button", {class: "btn small", type: "button", text: r.paused ? "Resume" : "Pause", "data-id": "pause-" + r.id,   // (focus kept on it: drawer.js recordSig)
          "aria-label": (r.paused ? "Resume" : "Pause") + " the schedule line " + mrSays(r),
          onclick: () => mrSend(`/${r.id}`, {method: "PATCH", body: JSON.stringify({paused: !r.paused})}, msg, m)}) : null,
        el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": "Remove the schedule line " + mrSays(r), onclick: async () => {
          if (!await ask({title: "Remove this from the schedule?", message: mrSays(r) + ". Today stops listing its doses, and any reminders leave your calendar at its next refresh.",
            action: "Remove", danger: true})) return;
          mrSend(`/${r.id}`, {method: "DELETE"}, msg, m);
        }})) : null);
  })));
  if (write && cur) out.push(el("div", {class: "btns"}, el("button", {class: "btn needs-server", type: "button", text: rs.length ? "Add another line" : "Add a schedule",
    onclick: () => medReminderForm(m, null)})));
  else if (write && !cur && m.stop) out.push(el("p", {class: "hint", text: "The records say this medication stopped, so no new schedule can be set for it."}));
  out.push(el("p", {class: "hint"}, "Today lists each dose it gives. With Remind me on, its times reach your calendar app through ",
    el("a", {href: `sharing.html?profile=${PROFILE.id}#calendar`, text: "Sharing → Calendar feed"}), ": Sanguity sends nothing itself."), msg);
  return out;
}
/* a typed amount: a number above 0 (a comma as the point too), null for none, or NaN */
const mrNum = v => (v = v.trim()) ? ((n => n && n.x > 0 ? n.x : NaN)(numberRead(v))) : null;   // (read as every number is, ranges.js, #874: "1,5" one and a half)
/* the add / edit form of a schedule line, in the drawer. r: the line (null: a new one) */
function medReminderForm(m, r) {
  const body = drawerHead("med", r ? "Edit schedule" : "New schedule", m.name);
  drawerStill = () => MEDS.some(x => x.key === m.key);   // (app.js, #517)
  const uid = Math.random().toString(36).slice(2, 8), id = n => `mr-${n}-${uid}`;
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  const times = el("div", {class: "mr-times"});
  const field = (t, attrs) => el("input", {type: "text", inputmode: t === "amount" ? "decimal" : "text", size: t === "amount" ? 5 : 9, maxlength: t === "amount" ? 12 : 40,
    autocomplete: "off", placeholder: t === "amount" ? "Amount" : "Unit", onkeydown: ev => { if (ev.key === "Enter") ev.preventDefault(); }, ...attrs});
  // each time named, and its id, by its place: one removed, those after it move up (#830: a second "Time 2" after it).
  // The only one's Remove is off, its reason its tip and description (#837: it did nothing, and said nothing)
  const named = () => [...times.children].forEach((row, i, all) => {
    const [input, amt, unit] = row.querySelectorAll("input"), rm = row.querySelector(".btn"), only = all.length < 2;
    input.id = id("t" + (i + 1)); input.setAttribute("aria-label", `Time ${i + 1}`); rm.setAttribute("aria-label", `Remove time ${i + 1}`);
    amt.setAttribute("aria-label", `Amount at time ${i + 1}`); unit.setAttribute("aria-label", `Unit at time ${i + 1}`);
    if (only) { rm.setAttribute("aria-disabled", "true"); rm.dataset.tip = "A schedule needs a time"; } else { rm.removeAttribute("aria-disabled"); delete rm.dataset.tip; }
  });
  const addTime = (v, a) => {
    // Enter in a time doesn't save the form, Save does (#853: a second Enter on Remove, in the next time's field, saved it)
    const input = el("input", {type: "time", required: true, onkeydown: ev => { if (ev.key === "Enter") ev.preventDefault(); }}); input.value = v || "";
    const amt = field("amount"), unit = field("unit");
    amt.value = a && a.amount != null ? String(a.amount) : ""; unit.value = a ? a.unit || "" : times.lastElementChild?.querySelectorAll("input")[2].value || "";
    // removed (never the only one), focus goes to the next time's field, or the one before (#830: it went to the page),
    // and that's said, as the next one takes its name (#853: "Time 2 removed; 2 times left")
    // the time and its Remove as before, its amount and unit on a line under them: the only time's tip (#837), above
    // Remove, covers none of them (#977)
    const row = el("div", {class: "mr-time"}, input, el("button", {class: "btn small", type: "button", text: "Remove", onclick: () => {
      const n = [...times.children].indexOf(row) + 1, left = times.children.length - 1;
      if (!left) return sayIn(msg, "A schedule needs a time, or As needed.");
      const to = row.nextElementSibling || row.previousElementSibling;
      row.remove(); more.hidden = false; named();
      to.querySelector("input").focus();
      sayIn(msg, `Time ${n} removed; ${left} time${left > 1 ? "s" : ""} left.`);
    }}), el("div", {}, amt, unit));
    times.append(row); more.hidden = times.children.length >= MR_MAX; named();
    return input;
  };
  // the time added said, in place of what was said last ("Time 2 removed; 1 time left" stayed, #867)
  const more = el("button", {class: "btn small mr-add", type: "button", text: "Add another time", onclick: () => { addTime("").focus(); sayIn(msg, `Time ${times.children.length} added.`); }});
  const days = new Set(r ? r.days : MR_DAYS.map(d => d[0]));
  const dayBox = el("div", {class: "chips mr-days", role: "group", "aria-label": "Days of the week"}, ...MR_DAYS.map(([k, short, long]) => {
    const b = el("button", {type: "button", class: "toggle", "aria-pressed": days.has(k), "aria-label": long, text: short, onclick: () => {
      days.has(k) ? days.delete(k) : days.add(k); b.setAttribute("aria-pressed", days.has(k)); }});
    b.style.setProperty("--tone", "var(--accent)");
    return b;
  }));
  const from = el("input", {type: "date", id: id("from"), required: true}); from.value = r ? r.from : zToday();
  const until = el("input", {type: "date", id: id("until")}); until.value = r && r.until ? r.until : "";
  const zone = r && r.tz ? r.tz : DATES.zone || browserZone();
  const row = (lbl, input) => el("div", {class: "jfield"}, el("label", {for: input.id, text: lbl}), input);
  // As needed: no times, no reminders; its usual amount, to start each dose logged with
  const need = el("input", {type: "checkbox", checked: r && r.asneeded || null, onchange: () => shown()});
  const usual = field("amount", {id: id("amt")}), usualUnit = field("unit", {id: id("unit")});
  usual.value = r && r.dose && r.dose.amount != null ? String(r.dose.amount) : ""; usualUnit.value = r && r.dose ? r.dose.unit || "" : "";
  // (#1103) a new line's Remind me is on only where this person has a calendar feed (any of theirs: GET me's
  // calendar_feeds) for its reminders to reach; without one it starts off, saying a feed is needed
  const feeds = ME?.calendar_feeds || 0;
  const remind = el("input", {type: "checkbox", checked: (r ? r.on : feeds > 0) ? "" : null});
  const timed = el("div", {class: "jfield"}, el("span", {class: "jlabel", text: "Times of day, each with its amount"}), times, more);
  const usualBox = el("div", {class: "jwhen"}, row("Usual amount (optional)", usual), row("Unit", usualUnit));
  const remindBox = feeds ? el("div", {class: "btns"}, el("label", {class: "toggle"}, remind, "Remind me in my calendar"))
    : el("div", {}, el("div", {class: "btns"}, el("label", {class: "toggle"}, remind, "Remind me in my calendar")),
      el("p", {class: "hint", id: id("feed")}, "Reminders reach your calendar app only through a calendar feed, and you have none yet: make one under ",
      el("a", {href: `sharing.html?profile=${PROFILE.id}#calendar`, text: "Sharing → Calendar feed"}), "."));
  const shown = () => { timed.hidden = remindBox.hidden = need.checked; usualBox.hidden = !need.checked; };
  const form = el("form", {class: "jform", novalidate: true},
    timed, el("div", {class: "btns"}, el("label", {class: "toggle"}, need, "As needed")), usualBox,
    el("div", {class: "jfield"}, el("span", {class: "jlabel", text: "Days"}), dayBox),
    el("div", {class: "jwhen"}, row("From", from), row("Until (optional)", until)), remindBox,
    el("p", {class: "hint", text: `Times are in ${zone}` + (r && r.tz ? ", the time zone it was set in." : ", your time zone (Settings → Dates and times).") +
      " With Remind me on, your calendar app shows each one as a repeating event with its amount, from the calendar feed."}),
    el("div", {class: "btns"}, el("button", {class: "btn primary needs-server", type: "submit", text: r ? "Save" : "Add to schedule"}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => openMedication(m)})), msg);
  if (r && r.times.length) r.times.forEach((t, i) => addTime(t, (r.amounts || [])[i])); else addTime("08:00");
  shown();
  const bad = (input, words) => { sayIn(msg, words, true); input.setAttribute("aria-invalid", "true"); input.addEventListener("input", () => input.removeAttribute("aria-invalid"), {once: true}); input.focus(); };
  form.onsubmit = ev => {
    ev.preventDefault();
    const rows = need.checked ? [] : [...times.children].map(t => t.querySelectorAll("input")).filter(t => t[0].value);
    if (!need.checked && !rows.length) { sayIn(msg, "Give at least one time of day.", true); times.querySelector("input").focus(); return; }
    for (const t of rows) if (Number.isNaN(mrNum(t[1].value))) return bad(t[1], `${t[1].getAttribute("aria-label")} isn't a number above 0.`);
    if (need.checked && Number.isNaN(mrNum(usual.value))) return bad(usual, "The usual amount isn't a number above 0.");
    if (!days.size) { sayIn(msg, "Pick at least one day.", true); dayBox.querySelector("button").focus(); return; }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(from.value)) { sayIn(msg, "Pick the date it starts from.", true); from.focus(); return; }
    const out = {med: m.key, times: rows.map(t => t[0].value.slice(0, 5)), amounts: rows.map(t => ({amount: mrNum(t[1].value), unit: t[2].value.trim()})),
      days: MR_DAYS.map(d => d[0]).filter(d => days.has(d)), start: from.value, until: until.value || null, tz: zone, asneeded: need.checked,
      dose: need.checked ? {amount: mrNum(usual.value), unit: usualUnit.value.trim()} : null, on: !need.checked && remind.checked};
    mrSend(r ? `/${r.id}` : "", {method: r ? "PUT" : "POST", body: JSON.stringify(out)}, msg, m);
  };
  body.append(form);
  showDrawer();
  (need.checked ? need : times.querySelector("input")).focus({preventScroll: true});
}
async function mrSend(path, opts, msg, m) {
  sayIn(msg, "Saving…");
  let r, j;
  try { r = await api(`profiles/${PROFILE.id}/medications/reminders${path}`, opts); j = await r.json().catch(() => ({})); }
  catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); return; }
  if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save the schedule (${r.status}).`, true); return; }
  storePut("reminders", j);
  medAgain(MEDS.find(x => x.key === m.key));
  const sw = opts.method === "PATCH" && JSON.parse(opts.body), now = j.reminders?.[0] || {};
  announce(opts.method === "DELETE" ? "Removed from the schedule." : !sw ? "Schedule saved."
    : "paused" in sw ? (now.paused ? "Paused: its doses leave Today and the counts." : "Resumed: its doses are on Today again.")
    : now.on ? "Remind me on." : "Remind me off.");
}
