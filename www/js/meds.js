/* Sanguity · the medication timeline (#50): each medication as spans, with its dose changes and pharmacy fills.
   The records (GET api/profiles/{pid}/data) list them as DATA.medications = [{name, key, rxnorm, spans: [{from, to,
   open, stopped, dose, title, status, prescriber, sources, ids, rxnorm}], changes: [{t, from, to, id}], fills: [{t,
   id, title, pharmacy, supply}], start, stop}] (server/medications.py works them out from the med and rx events).
   On the Timeline the Medications lane draws one row per medication: a bar for each span (a new shade where the
   dose changes), a cap where the records say it stopped, an arrow where they give no end, and a tick for each fill.
   On Trends the same rows can be shown under every chart (the "Medications" tick box, off until ticked).
   It shows what the records say, never whether a dose or a gap is right. Every date goes through dates.js.
   Reminders (#162, server/med_reminders.py): DATA.reminders = [{id, med (the medication's key), name, times, days,
   from, until, tz, on, by, at}], set in a medication's drawer by owners and editors for a current medication, listed
   by the calendar feed (Sharing → Calendar feed) as repeating events; the drawer says when one has ended.
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
    return {...m, i, spans, fills, changes: Array.isArray(m.changes) ? m.changes : [], _q: words.filter(Boolean).join(" ").toLowerCase()};
  });
}
/* where a span ends on screen: its last day, or today when the records give no end */
const medEnd = s => s.te ?? Math.max(s.ts, Date.now());
const medFrom = m => Math.min(...m.spans.map(s => s.ts), ...m.fills.map(f => f.ts));
const medTo = m => Math.max(...m.spans.map(medEnd), ...m.fills.map(f => f.ts + (f.supply || 0) * DAY));
/* q: the Timeline's search; the Labs charts show every medication, whatever was searched there (#192) */
const medsShown = (a, b, q = state.q) => MEDS.filter(m => (m.spans.length || m.fills.length) && medTo(m) >= a && medFrom(m) <= b && (!q || m._q.includes(q)));
const medShort = (n, max) => n.length > max ? n.slice(0, max - 1) + "…" : n;
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
    if (s.open && x2 <= R + 1) g.push(el("svg:path", {d: `M${b + 1},${y - 4}L${b + 6},${y}L${b + 1},${y + 4}Z`, fill: color, "fill-opacity": .6, "pointer-events": "none"}));
  });
  if (opts.selectable) medDoseMarks(g, m, x, y, L, R);   // doses logged, on the span (medlog.js, #235)
  for (const f of m.fills) {
    const fx = x(f.ts); if (fx < L - 2 || fx > R + 2) continue;
    if (f.supply) { const fe = Math.min(R, x(f.ts + f.supply * DAY)); if (fe > fx + 1) g.push(el("svg:line", {class: "medsupply", x1: Math.max(L, fx), x2: fe, y1: y + 7, y2: y + 7, "pointer-events": "none"})); }
    g.push(el("svg:path", {class: "medfill" + (opts.selectable ? " ev" : "") + (state.selEv === f.id ? " sel" : ""), d: `M${fx},${y + 3}L${fx + 3.5},${y + 9}L${fx - 3.5},${y + 9}Z`,
      "data-id": opts.selectable ? f.id : null, "data-med": m.i, "data-fill": f.id}));
  }
}
/* the Timeline's Medications lane (timeline.js asks TL_LANES for its height and drawing) */
TL_LANES.med = {
  height: () => MEDS.length ? 20 + Math.max(1, medsShown(state.from, state.to).length) * MED_ROW : 0,
  draw(g, S, top, h, c, w) {
    const rows = medsShown(state.from, state.to), narrow = w < 600;
    g.push(el("svg:text", {class: "lane-label", x: 0, y: top + 15, text: c.label}));
    g.push(el("svg:line", {class: "gridline", x1: S.L, x2: S.R, y1: top + h, y2: top + h, "stroke-opacity": .5}));
    if (!rows.length) { g.push(el("svg:text", {class: "medname", x: S.L + 4, y: top + 20 + MED_ROW / 2 + 4, text: "No medications in this date range"})); return; }
    rows.forEach((m, n) => {
      const y = top + 20 + n * MED_ROW + MED_ROW / 2 - 2;
      g.push(el("svg:text", {class: "medname", x: 8, y: y + 4, text: medShort(m.name, narrow ? 13 : 22)}, el("svg:title", {text: m.name})));
      medRow(g, m, S.x, y, S.L, S.R, c.color, {selectable: true});
    });
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

/* ---------------------------------------------------------------- Trends: the same rows under each chart, when ticked */
let medsOnTrends = false;
try { medsOnTrends = localStorage.getItem("sg-meds-trends") === "1"; } catch (e) {}
const medsInWindow = () => medsOnTrends && state.cats.has("med") ? medsShown(state.from, state.to, "") : [];
CHART_LAYERS.push({
  space: () => { const n = medsInWindow().length; return n ? 6 + n * 22 : 0; },
  draw(g, x, top, R) {
    medsInWindow().forEach((m, n) => {
      const y = top + 6 + n * 22 + 14;
      const first = Math.max(52, x(medFrom(m)));
      g.push(el("svg:text", {class: "medname", x: Math.min(first, R - 80) + 2, y: y - 7, text: medShort(m.name, 40)}));
      const before = g.length;
      medRow(g, m, x, y, 52, R, CAT_OF.med.color);
      for (const node of g.slice(before)) if (node.dataset?.med != null) {
        node.addEventListener("pointermove", ev => { ev.stopPropagation(); medTip(m, node.dataset, ev); });
        node.addEventListener("pointerleave", hideTip);
        node.addEventListener("click", ev => { ev.stopPropagation(); hideTip(); node.dataset.fill ? openEvent(byId.get(+node.dataset.fill)) : openMedication(m); });
        node.style.cursor = "pointer";
      }
    });
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
  const body = drawerHead("med", m.name, "Medication history · " + (m.spans.length ? `${fmtD(m.spans[0].ts)} – ${last.open ? "no end in the records" : fmtD(last.te)}` : `pharmacy fills only, from ${fmtD(m.fills[0].ts)}`));   // drawer.js (#270)
  drawer.dataset.med = m.key;   // (medShown)
  // its key facts, as every record's (#406): what the head says (when it started, that the records give no end) isn't
  // said again
  body.append(...present(drawerFacts([["Started", m.spans.length ? fmtD(m.spans[0].ts) : ""], ["Stopped", m.stop ? fmtD(dayTs(m.stop)) : ""],
    ["Dose changes", m.changes.length ? String(m.changes.length) : ""], ["Pharmacy fills", m.fills.length ? String(m.fills.length) : ""],
    ["RxNorm", (m.rxnorm || []).join(", ")]], "facts")));
  if (m.spans.length) {
    body.append(el("div", {class: "sect", text: "What the records list"}), scrollBox({class: "tablewrap", "aria-label": "What the records list", style: {maxHeight: "none"}}, el("table", {class: "data medspans"},
      el("thead", {}, el("tr", {}, ["When", "Listed as", "Status", "Prescribed by", "Source"].map(h => el("th", {text: h})))),
      el("tbody", {}, m.spans.map(s => el("tr", {class: "link", title: "Open this listing", onclick: () => openEvent(byId.get(s.ids[0]))},
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
  body.append(...medReminderSection(m));   // reminders through the calendar feed (#162)
  body.append(...medLogSection(m));        // the doses logged (medlog.js, #235)
  const notes = JOURNAL.filter(e => e.j.med && medKeyOf(e.j.med) === m.key);
  if (notes.length) body.append(el("div", {class: "sect", text: "Journal entries linked to it"}),
    ...notes.sort((a, b) => a.ts - b.ts).map(e => el("button", {class: "btn", type: "button", text: `${fmtD(e.ts)} · ${e.title}`, onclick: () => openEvent(e)})));
  body.append(el("p", {class: "hint", style: {color: "var(--ink2)"}, text: "What the records say, in order: a listing's dates may not be when a medication started or ended. This isn't medical advice."}),
    aboutWords("A clinic's medication list dates a medication from when it was recorded there, which may not be when it was started; a listing with no end date may still have ended."),
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


/* ---------------------------------------------------------------- reminders through the calendar feed (#162)
   Set here, for a current medication (the records give no end for its latest listing); the person's calendar app does
   the reminding, from the calendar feed. A reminder ends on its until day, or sooner on the day the records say the
   medication stopped, and the list here says which. Owners and editors set, change, turn off and remove them; every
   change is put in the records in place (store.js, #271) and this medication's drawer is drawn again. */
const MR_DAYS = [["mo", "Mon", "Monday"], ["tu", "Tue", "Tuesday"], ["we", "Wed", "Wednesday"], ["th", "Thu", "Thursday"],
  ["fr", "Fri", "Friday"], ["sa", "Sat", "Saturday"], ["su", "Sun", "Sunday"]];
const mrOf = m => (Array.isArray(DATA.reminders) ? DATA.reminders : []).filter(r => r && r.med === m.key);
const mrCurrent = m => !!m.spans.length && !!m.spans[m.spans.length - 1].open;
/* "8:00 AM" in this person's language, for a wall-clock "HH:MM" (no zone: it is the reminder's own) */
const mrTime = t => { const [h, n] = t.split(":").map(Number); return _fmt(Date.UTC(2000, 0, 1, h, n), {hour: "numeric", minute: "2-digit", timeZone: "UTC"}); };
const mrAnd = xs => xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs.at(-1);
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
/* "8:00 AM and 8:00 PM every day, from 18 Nov 2026, no end date" */
const mrSays = r => `${mrAnd(r.times.map(mrTime))} ${mrDays(r.days)}, from ${fmtD(dayTs(r.from))}` + (r.until ? ` until ${fmtD(dayTs(r.until))}` : ", no end date");
function mrState(r, m) {
  const end = mrEnds(r, m);
  if (end && end.why === "stopped") return {cls: "ended", text: `Ended ${fmtD(dayTs(end.day))}: the records say this medication stopped then`};
  if (end && end.day < zToday()) return {cls: "ended", text: `Ended ${fmtD(dayTs(end.day))}`};
  if (!r.on) return {cls: "off", text: "Off: not in the calendar feed"};
  return {cls: "on", text: "On: in the calendar feed"};
}
function medReminderSection(m) {
  const rs = mrOf(m), write = canEdit(), cur = mrCurrent(m);
  if (!rs.length && !(write && cur)) return [];
  const out = [el("div", {class: "sect", text: "Reminders in your calendar"})];
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  if (rs.length) out.push(el("ul", {class: "mr-list"}, ...rs.map(r => {
    const st = mrState(r, m), zone = r.tz && r.tz !== (DATES.zone || browserZone()) ? ` (${r.tz} time)` : "";
    return el("li", {class: "mr-item " + st.cls},
      el("span", {class: "mr-says", text: mrSays(r) + zone}),
      el("span", {class: "mr-state", text: st.text}),
      write ? el("span", {class: "mr-acts needs-server"},   // what saves, off while offline (offline.js, #449)
        st.cls !== "ended" || mrEnds(r, m)?.why === "until" ? el("button", {class: "btn small", type: "button", text: "Edit",
          "aria-label": "Edit the reminder " + mrSays(r), onclick: () => medReminderForm(m, r)}) : null,
        st.cls !== "ended" ? el("button", {class: "btn small", type: "button", text: r.on ? "Turn off" : "Turn on",
          "aria-label": (r.on ? "Turn off" : "Turn on") + " the reminder " + mrSays(r),
          onclick: () => mrSend(`/${r.id}`, {method: "PATCH", body: JSON.stringify({on: !r.on})}, msg, m)}) : null,
        el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": "Remove the reminder " + mrSays(r), onclick: async () => {
          if (!await ask({title: "Remove this reminder?", message: mrSays(r) + ". It leaves your calendar at its next refresh.", action: "Remove", danger: true})) return;
          mrSend(`/${r.id}`, {method: "DELETE"}, msg, m);
        }})) : null);
  })));
  if (write && cur) out.push(el("div", {class: "btns"}, el("button", {class: "btn needs-server", type: "button", text: rs.length ? "Add another reminder" : "Add a reminder",
    onclick: () => medReminderForm(m, null)})));
  else if (write && !cur && m.stop) out.push(el("p", {class: "hint", text: "The records say this medication stopped, so no new reminders can be set for it."}));
  out.push(el("p", {class: "hint"}, "They reach your calendar app through ",
    el("a", {href: `sharing.html?profile=${PROFILE.id}#calendar`, text: "Sharing → Calendar feed"}), ": Sanguity sends nothing itself."), msg);
  return out;
}
/* the add / edit form, in the drawer. r: the reminder (null: a new one) */
function medReminderForm(m, r) {
  const body = drawerHead("med", r ? "Edit reminder" : "New reminder", m.name);
  drawerStill = () => MEDS.some(x => x.key === m.key);   // (app.js, #517)
  const uid = Math.random().toString(36).slice(2, 8), id = n => `mr-${n}-${uid}`;
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  const times = el("div", {class: "mr-times"});
  const addTime = v => {
    const n = times.children.length + 1;
    const input = el("input", {type: "time", id: id("t" + n), required: true, "aria-label": `Time ${n}`}); input.value = v || "";
    const row = el("div", {class: "mr-time"}, input, el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": `Remove time ${n}`,
      onclick: () => { if (times.children.length > 1) { row.remove(); more.hidden = false; } }}));
    times.append(row); more.hidden = times.children.length >= 6;
    return input;
  };
  const more = el("button", {class: "btn small mr-add", type: "button", text: "Add another time", onclick: () => addTime("").focus()});
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
  const form = el("form", {class: "jform", novalidate: true},
    el("div", {class: "jfield"}, el("span", {class: "jlabel", text: "Times of day"}), times, more),
    el("div", {class: "jfield"}, el("span", {class: "jlabel", text: "Days"}), dayBox),
    el("div", {class: "jwhen"}, row("From", from), row("Until (optional)", until)),
    el("p", {class: "hint", text: `Times are in ${zone}` + (r && r.tz ? ", the time zone it was set in." : ", your time zone (Settings → Dates and times).") +
      " Your calendar app shows each one as a repeating event, from the calendar feed."}),
    el("div", {class: "btns"}, el("button", {class: "btn primary needs-server", type: "submit", text: r ? "Save" : "Add reminder"}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => openMedication(m)})), msg);
  for (const t of r ? r.times : ["08:00"]) addTime(t);
  form.onsubmit = ev => {
    ev.preventDefault();
    const ts = [...times.querySelectorAll("input")].map(i => i.value).filter(Boolean);
    if (!ts.length) { sayIn(msg, "Give at least one time of day.", true); times.querySelector("input").focus(); return; }
    if (!days.size) { sayIn(msg, "Pick at least one day.", true); dayBox.querySelector("button").focus(); return; }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(from.value)) { sayIn(msg, "Pick the date it starts from.", true); from.focus(); return; }
    const out = {med: m.key, times: ts.map(t => t.slice(0, 5)), days: MR_DAYS.map(d => d[0]).filter(d => days.has(d)), start: from.value,
      until: until.value || null, tz: zone, on: r ? r.on : true};
    mrSend(r ? `/${r.id}` : "", {method: r ? "PUT" : "POST", body: JSON.stringify(out)}, msg, m);
  };
  body.append(form);
  showDrawer();
  times.querySelector("input").focus({preventScroll: true});
}
async function mrSend(path, opts, msg, m) {
  sayIn(msg, "Saving…");
  let r, j;
  try { r = await api(`profiles/${PROFILE.id}/medications/reminders${path}`, opts); j = await r.json().catch(() => ({})); }
  catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); return; }
  if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save the reminder (${r.status}).`, true); return; }
  storePut("reminders", j);
  medAgain(MEDS.find(x => x.key === m.key));
  announce(opts.method === "DELETE" ? "Reminder removed." : "Reminder saved.");
}
