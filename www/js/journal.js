/* Sanguity · the journal (#49): notes, symptoms and side effects the person writes, beside the records.
   The records (GET api/profiles/{pid}/data) list them as DATA.journal = [{id, kind, t, title, severity, note, med, by,
   at, changed, changed_by}]; each becomes an event of kind "journal" in EV (id: minus the entry's id, so it never
   meets a record's), drawn in its own timeline lane and opened in the drawer like any event. Trends shows them as
   markers in a row under each chart's time axis (CHART_LAYERS, trends.js). Severity is shown by the marker's size,
   the kind by its shape (note: square, symptom: circle, side effect: diamond), and both are in its label: never by
   colour alone. Owners and editors add, change and delete entries (server/routes_journal.py); viewers read them.
   Plain script, loaded in order by index.html after trends.js; all files share one global scope. */
"use strict";
const J_KINDS = [["note", "Note"], ["symptom", "Symptom"], ["side_effect", "Side effect"]];
const J_LABEL = Object.fromEntries(J_KINDS);
let JOURNAL = [];   // the entries as events: {id: -entry id, k: "journal", t, ts, te, title, sub, j: the entry}
const jTime = t => zParse(t);   // this person's wall-clock time (dates.js, #42); a day without a time sits at midday
const jRated = j => j.kind !== "note" && Number.isInteger(j.severity);
const jSev = j => jRated(j) ? `severity ${j.severity}/10` : "";
const jWhen = j => /T/.test(j.t) ? fmtDT(jTime(j.t)) : fmtD(jTime(j.t));
function jTitle(j) {
  if (j.title) return j.title;
  const n = (j.note || "").replace(/\s+/g, " ").trim();
  return n.length > 60 ? n.slice(0, 59) + "…" : n || "Note";
}
/* "Symptom: Nausea, severity 6/10, Ondansetron 4 mg, 3 Nov 2026" for labels and screen readers */
const jSay = j => [`${J_LABEL[j.kind]}: ${jTitle(j)}`, jSev(j), j.med, jWhen(j)].filter(Boolean).join(", ");

function journalLoad() {
  JOURNAL = (Array.isArray(DATA.journal) ? DATA.journal : []).filter(j => j && J_LABEL[j.kind] && isFinite(jTime(j.t))).map(j => {
    const e = {id: -j.id, k: "journal", t: j.t, title: jTitle(j), sub: [J_LABEL[j.kind], jSev(j), j.med].filter(Boolean).join(" · "), j};
    e.ts = e.te = jTime(j.t);
    e._q = [e.title, j.note, j.med, J_LABEL[j.kind], "journal"].filter(Boolean).join(" ").toLowerCase();
    byId.set(e.id, e);
    return e;
  });
  EV = EV.concat(JOURNAL);
  $("#journalBtn").hidden = false;
  $("#journalBtn").onclick = () => openJournalList();
}

/* ---------------------------------------------------------------- marks: shape by kind, size by severity */
const jRadius = j => jRated(j) ? 3 + j.severity * .45 : 4;   // 3 (none) to 7.5 (worst)
function jShape(j, x, y, attrs) {
  const r = jRadius(j);
  if (j.kind === "symptom") return el("svg:circle", {cx: x, cy: y, r, ...attrs});
  if (j.kind === "side_effect") return el("svg:path", {d: `M${x},${y - r * 1.25}L${x + r * 1.25},${y}L${x},${y + r * 1.25}L${x - r * 1.25},${y}Z`, ...attrs});
  return el("svg:rect", {x: x - r, y: y - r, width: 2 * r, height: 2 * r, rx: 1.5, ...attrs});
}
/* the timeline lane's mark (timeline.js picks it up by data-id like any event's) */
function journalMark(e, x, y, selected) {
  return jShape(e.j, x, y, {class: "ev jmark" + (selected ? " sel" : ""), fill: "var(--c-journal)", "fill-opacity": jRated(e.j) ? .25 + e.j.severity * .065 : .85,
    stroke: "var(--c-journal)", "stroke-width": 1.5, "data-id": e.id});
}

/* Trends: one row of markers under every chart's time axis, toggled beside "Procedures & imaging" */
let jOnTrends = true;
try { jOnTrends = localStorage.getItem("sg-journal-trends") !== "0"; } catch (e) {}
const jInWindow = () => jOnTrends ? JOURNAL.filter(e => e.ts >= state.from && e.ts <= state.to && visibleJournal(e)) : [];
const visibleJournal = e => state.cats.has("journal");   // the Journal chip on the Timeline hides them everywhere
CHART_LAYERS.push({
  space: () => jInWindow().length ? 18 : 0,
  draw(g, x, top, R) {
    const y = top + 9;
    g.push(el("svg:text", {class: "jrow-label", x: 46, y: y + 3.5, "text-anchor": "end", text: "Journal"}));
    for (const e of jInWindow()) {
      const mx = x(e.ts); if (mx < 40 || mx > R + 4) continue;
      const m = jShape(e.j, mx, y, {class: "jmark", fill: "var(--c-journal)", "fill-opacity": jRated(e.j) ? .25 + e.j.severity * .065 : .85,
        stroke: "var(--c-journal)", "stroke-width": 1.5, tabindex: 0, role: "button", "aria-label": jSay(e.j), "data-jid": e.id});
      m.addEventListener("pointermove", ev => { ev.stopPropagation(); showEvTip(e, ev); });
      m.addEventListener("pointerleave", hideTip);
      m.addEventListener("click", ev => { ev.stopPropagation(); hideTip(); openEvent(e); });
      m.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openEvent(e); } });
      g.push(m);
    }
  },
});
{
  const marks = $("#optMarks")?.closest("label");
  if (marks) {
    const box = el("input", {type: "checkbox", id: "optJournal", checked: jOnTrends || null, onchange: ev => {
      jOnTrends = ev.target.checked; try { localStorage.setItem("sg-journal-trends", jOnTrends ? "1" : "0"); } catch (e) {} renderCharts(); }});
    marks.after(el("label", {id: "optJournalLabel"}, box, " Journal"));
  }
  const legend = document.querySelector("#view-labs .legend");
  if (legend) legend.append(el("span", {class: "jlegend"}, el("svg:svg", {viewBox: "0 0 30 12", width: 30, height: 12, "aria-hidden": "true"},
    jShape({kind: "symptom", severity: 2}, 5, 6, {fill: "var(--c-journal)", "fill-opacity": .4, stroke: "var(--c-journal)"}),
    jShape({kind: "symptom", severity: 8}, 20, 6, {fill: "var(--c-journal)", "fill-opacity": .8, stroke: "var(--c-journal)"})),
    "Journal: bigger = more severe; ● symptom, ◆ side effect, ■ note"));
}

/* ---------------------------------------------------------------- the drawer: one entry, the list, the form */
const jCanWrite = () => !!PROFILE && (PROFILE.role === "owner" || PROFILE.role === "editor");
function jDrawer(title, sub) {
  $("#dkey").style.background = "var(--c-journal)"; $("#dtitle").textContent = title; $("#dsub").textContent = sub;
  const body = $("#dbody"); body.replaceChildren(); body.scrollTop = 0; body.classList.remove("fill");
  return body;
}
function jSeverityMeter(n) {
  return el("span", {class: "jsev", role: "img", "aria-label": `Severity ${n} out of 10`},
    el("span", {class: "jsev-bar", "aria-hidden": "true"}, el("span", {style: {width: n * 10 + "%"}})), el("b", {text: `${n}/10`}));
}
function openJournal(e) {
  const j = e.j;
  state.selEv = e.id;
  const body = jDrawer(jTitle(j), `${J_LABEL[j.kind]} · ${jWhen(j)}`);
  body.append(el("dl", {class: "kv"},
    el("dt", {text: "Kind"}), el("dd", {text: J_LABEL[j.kind]}),
    el("dt", {text: "When"}), el("dd", {text: jWhen(j)}),
    jRated(j) ? [el("dt", {text: "Severity"}), el("dd", {}, jSeverityMeter(j.severity))] : null,
    j.med ? [el("dt", {text: "Medication"}), el("dd", {text: j.med})] : null,
    el("dt", {text: "Written by"}), el("dd", {text: `${j.by || "someone"} · ${fmtDT(j.at * 1000)}` + (j.changed ? ` (changed${j.changed_by ? " by " + j.changed_by : ""} ${fmtDT(j.changed * 1000)})` : "")})));
  if (j.note) body.append(el("div", {class: "sect", text: j.kind === "note" ? "Note" : "Notes"}), el("p", {class: "jnote", text: j.note}));
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  const acts = el("div", {class: "btns"});
  if (jCanWrite()) {
    acts.append(el("button", {class: "btn", type: "button", text: "Edit", onclick: () => journalForm(j, {}, () => openEvent(e))}),
      el("button", {class: "btn", type: "button", text: "Delete", onclick: () => {
        acts.replaceChildren(el("span", {class: "hint", text: "Delete this entry? This can't be undone. "}),
          el("button", {class: "btn primary", type: "button", text: "Delete", onclick: () => jSend(`/${j.id}`, {method: "DELETE"}, msg, {list: true})}),
          el("button", {class: "btn", type: "button", text: "Keep it", onclick: () => openEvent(e)}));
        acts.querySelector(".btn.primary").focus();
      }}));
  }
  acts.append(el("button", {class: "btn", type: "button", text: "Day on the timeline", onclick: () => { setRange(e.ts - 15 * DAY, e.ts + 15 * DAY); switchTab("timeline"); }}),
    el("button", {class: "btn", type: "button", text: "All journal entries", onclick: () => openJournalList()}));
  body.append(acts, msg);
  showDrawer();
  renderTimeline();
}

const jFilter = {kind: "", med: "", q: "", inRange: false};
function openJournalList() {
  state.selEv = null;
  const body = jDrawer("Journal", JOURNAL.length ? `${JOURNAL.length} entr${JOURNAL.length === 1 ? "y" : "ies"} written by the people who use this profile` : "Nothing written yet");
  const meds = [...new Set(JOURNAL.map(e => e.j.med).filter(Boolean))].sort();
  const kind = el("select", {"aria-label": "Kind of entry"}, el("option", {value: "", text: "All kinds"}), ...J_KINDS.map(([v, t]) => el("option", {value: v, text: t + "s"})));
  const med = el("select", {"aria-label": "Medication", hidden: !meds.length || null}, el("option", {value: "", text: "Any medication"}), ...meds.map(m => el("option", {value: m, text: m})));
  const q = el("input", {type: "search", placeholder: "Search the journal…", "aria-label": "Search the journal"});
  const shown = el("label", {class: "jinrange"}, el("input", {type: "checkbox", checked: jFilter.inRange || null}), " Only the dates shown");
  kind.value = jFilter.kind; med.value = jFilter.med; q.value = jFilter.q;
  const list = el("div", {class: "jlist", role: "list"}), count = el("p", {class: "hint", role: "status"});
  const draw = () => {
    Object.assign(jFilter, {kind: kind.value, med: med.value, q: q.value.trim().toLowerCase(), inRange: shown.firstChild.checked});
    const v = JOURNAL.filter(e => (!jFilter.kind || e.j.kind === jFilter.kind) && (!jFilter.med || e.j.med === jFilter.med) &&
      (!jFilter.q || e._q.includes(jFilter.q)) && (!jFilter.inRange || e.ts >= state.from && e.ts <= state.to)).sort((a, b) => b.ts - a.ts);
    count.textContent = JOURNAL.length ? `${v.length} of ${JOURNAL.length} shown` : "";
    list.replaceChildren(...v.map(e => el("button", {class: "jrow", type: "button", role: "listitem", onclick: () => openEvent(e), "aria-label": jSay(e.j)},
      el("span", {class: "d", text: fmtD(e.ts)}),
      el("svg:svg", {class: "jicon", viewBox: "0 0 18 18", width: 18, height: 18, "aria-hidden": "true"},
        jShape(e.j, 9, 9, {fill: "var(--c-journal)", "fill-opacity": jRated(e.j) ? .25 + e.j.severity * .065 : .85, stroke: "var(--c-journal)"})),
      el("span", {class: "t"}, el("b", {text: e.title}), el("span", {class: "s", text: [J_LABEL[e.j.kind], e.j.med].filter(Boolean).join(" · ")})),
      jRated(e.j) ? el("span", {class: "badge", text: `${e.j.severity}/10`}) : el("span"))));
    if (!v.length) list.append(el("div", {class: "empty", text: JOURNAL.length ? "No entries match." : jCanWrite() ? "Add a note, a symptom or a side effect to start the journal." : "No one has written in this journal yet."}));
  };
  for (const c of [kind, med]) c.onchange = draw;
  q.oninput = draw; shown.firstChild.onchange = draw;
  body.append(
    jCanWrite() ? el("div", {class: "btns"}, el("button", {class: "btn primary", type: "button", text: "Add an entry", onclick: () => journalForm(null, {}, openJournalList)})) : null,
    el("div", {class: "jfilters"}, kind, med, q, shown), count, list,
    el("p", {class: "hint", text: "The journal is what you wrote, kept with the records: it isn't part of any import, so undoing an import leaves it. It's in “Export everything” and in backups."}));
  draw();
  showDrawer();
  renderTimeline();
}

/* the add / edit form. j: the entry (null: a new one); preset: {kind, t} for a new one; back(): what Cancel shows */
function journalForm(j, preset, back) {
  const isNew = !j, cur = j || {kind: preset.kind || "symptom", t: preset.t || isoDay(Date.now()), title: "", severity: 5, note: "", med: null};
  const body = jDrawer(isNew ? "New journal entry" : "Edit journal entry", isNew ? "A note, a symptom or a side effect" : `${J_LABEL[cur.kind]} · ${jWhen(cur)}`);
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  const uid = Math.random().toString(36).slice(2, 8), id = n => `j-${n}-${uid}`;
  const kinds = el("div", {class: "seg jkinds", role: "radiogroup", "aria-label": "Kind of entry"});
  let kind = cur.kind;
  const date = el("input", {type: "date", id: id("date"), required: true}), time = el("input", {type: "time", id: id("time")});
  date.value = cur.t.slice(0, 10); time.value = cur.t.length > 10 ? cur.t.slice(11, 16) : "";
  const title = el("input", {type: "text", id: id("title"), maxlength: 200, autocomplete: "off"}); title.value = cur.title || "";
  const sev = el("input", {type: "range", id: id("sev"), min: 0, max: 10, step: 1});
  sev.value = Number.isInteger(cur.severity) ? cur.severity : 5;
  const sevOut = el("output", {for: id("sev"), class: "jsev-out"});
  const sevSay = () => { const n = +sev.value; sevOut.textContent = `${n}/10 ${n === 0 ? "· none" : n <= 3 ? "· mild" : n <= 6 ? "· moderate" : n <= 9 ? "· severe" : "· worst"}`; sev.setAttribute("aria-valuetext", sevOut.textContent); };
  sev.oninput = sevSay; sevSay();
  const medNames = [...new Set(EV.filter(e => e.k === "med" || e.k === "rx").map(e => e.title).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  if (cur.med && !medNames.includes(cur.med)) medNames.unshift(cur.med);   // linked before its import was undone: kept
  const med = el("select", {id: id("med")}, el("option", {value: "", text: medNames.length ? "None" : "No medications in the records"}), ...medNames.map(m => el("option", {value: m, text: m})));
  med.value = cur.med || "";
  const note = el("textarea", {id: id("note"), rows: 4, maxlength: 5000}); note.value = cur.note || "";
  const row = (lbl, input, cls) => el("div", {class: "jfield" + (cls ? " " + cls : "")}, el("label", {for: input.id, text: lbl}), input);
  const rated = [row("What was it?", title), el("div", {class: "jfield"}, el("label", {for: sev.id}, "Severity ", el("span", {class: "hint", text: "0 none – 10 worst"})), el("div", {class: "jsev-in"}, sev, sevOut)), row("Linked medication (optional)", med)];
  const noteRow = row("Note", note);
  const setKind = k => {
    kind = k;
    for (const b of kinds.children) { b.setAttribute("aria-checked", b.dataset.kind === k); b.tabIndex = b.dataset.kind === k ? 0 : -1; }
    for (const r of rated) r.hidden = k === "note";
    noteRow.querySelector("label").textContent = k === "note" ? "Note" : "Note (optional)";
    title.placeholder = k === "side_effect" ? "e.g. Nausea" : k === "symptom" ? "e.g. Headache" : "";
  };
  kinds.append(...J_KINDS.map(([k, t]) => el("button", {type: "button", role: "radio", "data-kind": k, text: t, onclick: () => setKind(k),
    onkeydown: ev => { const i = J_KINDS.findIndex(([x]) => x === kind), d = ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : ev.key === "ArrowLeft" || ev.key === "ArrowUp" ? -1 : 0;
      if (d) { ev.preventDefault(); const n = J_KINDS[(i + d + J_KINDS.length) % J_KINDS.length][0]; setKind(n); kinds.querySelector(`[data-kind="${n}"]`).focus(); } }})));
  setKind(kind);
  const form = el("form", {class: "jform", novalidate: true},
    el("div", {class: "jfield"}, el("span", {class: "jlabel", text: "Kind"}), kinds),
    el("div", {class: "jwhen"}, row("Date", date), row("Time (optional)", time)),
    ...rated, noteRow,
    el("div", {class: "btns"}, el("button", {class: "btn primary", type: "submit", text: isNew ? "Add to journal" : "Save"}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => back()})), msg);
  form.onsubmit = ev => {
    ev.preventDefault();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date.value)) { msg.textContent = "Pick a date."; date.focus(); return; }
    const out = {kind, t: date.value + (time.value ? "T" + time.value.slice(0, 5) : ""), note: note.value};
    if (kind !== "note") Object.assign(out, {title: title.value, severity: +sev.value, med: med.value || null});
    else if (j && j.title) out.title = j.title;
    jSend(isNew ? "" : `/${j.id}`, {method: isNew ? "POST" : "PUT", body: JSON.stringify(out)}, msg, isNew ? {created: true} : {entry: j.id});
  };
  body.append(form);
  showDrawer();
  (kind === "note" ? note : title).focus({preventScroll: true});
}

/* every change reloads the records (as edits do, drawer.js) and reopens what was being looked at */
async function jSend(path, opts, msg, reopen) {
  msg.textContent = "Saving…";
  try {
    const r = await api(`profiles/${PROFILE.id}/journal${path}`, opts);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { msg.textContent = typeof j.detail === "string" ? j.detail : `Couldn't save the entry (${r.status}).`; return; }
    if (reopen.created) reopen = {entry: j.id};
    try { sessionStorage.setItem("sg-after-journal", JSON.stringify(reopen)); } catch (e) {}
    location.reload();
  } catch (err) { msg.textContent = "Couldn't reach the server: " + err.message; }
}
function journalResume() {
  let r = null;
  try { r = JSON.parse(sessionStorage.getItem("sg-after-journal") || "null"); sessionStorage.removeItem("sg-after-journal"); } catch (e) {}
  if (!r) return;
  if (r.entry && byId.has(-r.entry)) openEvent(byId.get(-r.entry));
  else openJournalList();
}

/* in the drawer of any event: write a journal note dated to it */
function journalAddAction(e) {
  if (!jCanWrite() || !e || !isFinite(e.ts)) return el("span");
  const t = /T\d{2}:\d{2}/.test(e.t || "") ? isoDay(e.ts) + "T" + (z => `${String(z.getHours()).padStart(2, "0")}:${String(z.getMinutes()).padStart(2, "0")}`)(zdate(e.ts)) : isoDay(e.ts);
  return el("div", {class: "jadd"}, el("button", {class: "btn", type: "button", text: "Add a journal note for this day",
    title: "A note, symptom or side effect, dated to this event", onclick: () => journalForm(null, {kind: "note", t}, () => openEvent(e))}));
}
