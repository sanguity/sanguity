/* Sanguity · the journal (#49): notes, symptoms and side effects the person writes, beside the records; and (#161) a
   day's mood and energy, symptom groups logged in one go, where on the body, and photos.
   The records (GET api/profiles/{pid}/data) list them as DATA.journal = [{id, kind, t, title, severity, note, med, by,
   at, changed, changed_by, where?, group?, photos?, mood?, energy?}]; each becomes an event of kind "journal" in EV (a
   mood: kind "mood", its own lane; id: minus the entry's id, so it never meets a record's), drawn in its timeline lane
   and opened in the drawer like any event. Labs shows them as markers in a row of its event track over the charts
   (CHART_LAYERS, trends.js; event-track.js, #294), and moods in a row of their own. Severity is shown by the marker's size, the kind by its
   shape (util.js KIND_SHAPE, #301: symptom a star, side effect a hexagon, note a bar), a mood and energy by bars'
   heights, and each in its label: never by colour alone. Owners and editors add, change and delete entries
   (server/routes_journal.py); viewers read them. The journal's home is the Timeline tab's Journal view (#277), a card with its filters and search, and the
   body map of where symptoms have been in the dates shown (journal-body.js); Add → Journal entry opens the form for a
   new one from any tab (#173). Symptom groups are the setting journal.groups (CFG.journal.groups), changed from that card.
   Plain script, loaded in order by index.html after trends.js and journal-body.js; all files share one global scope. */
"use strict";
const J_KINDS = [["note", "Note"], ["symptom", "Symptom"], ["side_effect", "Side effect"], ["mood", "Mood"]];
const J_LABEL = Object.fromEntries(J_KINDS);
let JOURNAL = [];   // the entries as events: {id: -entry id, k: "journal" | "mood", t, ts, te, title, sub, j: the entry}
const jTime = t => zParse(t);   // this person's wall-clock time (dates.js, #42); a day without a time sits at midday
const jRated = j => (j.kind === "symptom" || j.kind === "side_effect") && Number.isInteger(j.severity);
const jSev = j => jRated(j) ? `severity ${j.severity}/10` : "";
const jWhen = j => /T/.test(j.t) ? fmtDT(jTime(j.t)) : fmtD(jTime(j.t));
const jMoodSay = j => [j.mood != null ? `mood ${j.mood}/10` : "", j.energy != null ? `energy ${j.energy}/10` : ""].filter(Boolean).join(", ");
const jWhere = j => (j.where || []).map(jbLabel).join(", ");
const jPhotos = j => (j.photos || []).length ? `${j.photos.length} photo${j.photos.length > 1 ? "s" : ""}` : "";
function jTitle(j) {
  if (j.kind === "mood") { const s = jMoodSay(j); return s ? s[0].toUpperCase() + s.slice(1) : "Mood"; }
  if (j.title) return j.title;
  const n = (j.note || "").replace(/\s+/g, " ").trim();
  return n.length > 60 ? n.slice(0, 59) + "…" : n || "Note";
}
/* "Symptom: Nausea, severity 6/10, Ondansetron 4 mg, where: Upper belly, 3 Nov 2026" for labels and screen readers */
const jSay = j => j.kind === "mood" ? `${jTitle(j)}, ${jWhen(j)}` :
  [`${J_LABEL[j.kind]}: ${jTitle(j)}`, jSev(j), j.med, j.where?.length && `where: ${jWhere(j)}`, j.group && `in “${j.group}”`, jPhotos(j), jWhen(j)].filter(Boolean).join(", ");
const jGroups = () => Array.isArray(CFG.journal?.groups) ? CFG.journal.groups : [];

/* the entries as events, in place of any from before (a change put in by store.js, #271) */
function journalLoad() {
  for (const e of JOURNAL) byId.delete(e.id);
  EV = EV.filter(e => e.k !== "journal" && e.k !== "mood");
  JOURNAL = (Array.isArray(DATA.journal) ? DATA.journal : []).filter(j => j && J_LABEL[j.kind] && isFinite(jTime(j.t))).map(j => {
    const e = {id: -j.id, k: j.kind === "mood" ? "mood" : "journal", t: j.t, title: jTitle(j), j,
      sub: j.kind === "mood" ? (j.note || "").replace(/\s+/g, " ").slice(0, 80) : [J_LABEL[j.kind], jSev(j), j.med, j.group, jWhere(j), jPhotos(j)].filter(Boolean).join(" · ")};
    e.ts = e.te = jTime(j.t);
    e._q = [e.title, j.note, j.med, j.group, jWhere(j), J_LABEL[j.kind], j.kind === "mood" ? "energy" : "", "journal"].filter(Boolean).join(" ").toLowerCase();
    byId.set(e.id, e);
    return e;
  });
  EV = EV.concat(JOURNAL);
}
// Add → Journal entry (nav.js, which offers it to the profile's owners and editors, #401): the new-entry form, from
// any tab, or on a profile with no records yet; the panel gives focus back to the Add button
$("#journalBtn").onclick = () => { $("#addMenuBtn")?.focus({preventScroll: true}); journalNew(); };

/* ---------------------------------------------------------------- marks: shape by kind, size by severity */
const jRadius = j => jRated(j) ? 3 + j.severity * .45 : 4;   // 3 (none) to 7.5 (worst)
const jShape = (j, x, y, attrs) => shapeMark(KIND_SHAPE[j.kind] || "bar", x, y, jRadius(j), attrs);
/* a mood's mark (#161): two bars from a base line, mood filled and energy outlined, each as tall as its 0-10 */
function jMoodBars(j, x, base, tall, attrs = {}) {
  const bar = (v, dx, filled) => v == null ? null : el("svg:rect", {x: x + dx, y: base - 1.5 - v / 10 * tall, width: 3.5, height: 1.5 + v / 10 * tall, rx: .8,
    fill: filled ? "var(--c-journal)" : "var(--surface)", stroke: "var(--c-journal)", "stroke-width": 1});
  return el("svg:g", {class: "jmood", ...attrs}, el("svg:rect", {x: x - 5, y: base - tall - 3, width: 10, height: tall + 4, fill: "transparent"}),
    bar(j.mood, -4, true), bar(j.energy, .5, false));
}
/* the timeline lane's mark (timeline.js picks it up by data-id like any event's) */
function journalMark(e, x, y, selected) {
  return jShape(e.j, x, y, {class: "ev jmark" + (selected ? " sel" : ""), fill: "var(--c-journal)", "fill-opacity": jRated(e.j) ? .25 + e.j.severity * .065 : .85,
    stroke: "var(--c-journal)", "stroke-width": 1.5, "data-id": e.id});
}
/* the Timeline's Mood and energy lane (#161): a day's mood and energy as bars, 0 at the lane's floor, 10 at its top */
TL_LANES.mood = {
  shows: () => EV.some(e => e.k === "mood" && !e.hidden && inRange(e) && matchQ(e)),
  height: (w, S) => JOURNAL.some(e => e.k === "mood") ? (S.over ? 40 : Math.max(44, 30 * S.k)) : 0,
  draw(g, S, top, h) {
    const base = top + h - 6, tall = Math.min(30, h - 14);
    g.push(el("svg:line", {class: "gridline", x1: S.L, x2: S.R, y1: base - tall - 1.5, y2: base - tall - 1.5, "stroke-opacity": .3, "stroke-dasharray": "2 3"}));
    for (const e of EV.filter(e => e.k === "mood" && inRange(e) && matchQ(e))) {
      const x = S.x(e.ts);
      if (x < S.L - 4 || x > S.R + 4) continue;
      g.push(jMoodBars(e.j, x, base, tall, {"data-id": e.id, class: "ev jmood" + (state.selEv === e.id ? " sel" : "")}));
    }
  },
};

/* Labs: one row of markers on the event track over the charts (#294), toggled beside "Procedures & imaging"; moods in
   a second row (#161) */
let jOnTrends = true;
try { jOnTrends = localStorage.getItem("sg-journal-trends") !== "0"; } catch (e) {}
const visibleJournal = e => state.cats.has(e.k);   // the Journal (and Mood) chips on the Timeline hide them everywhere
const jInWindow = (k = "journal") => jOnTrends ? JOURNAL.filter(e => e.k === k && e.ts >= state.from && e.ts <= state.to && visibleJournal(e)) : [];
function jTrendsMark(m, e) {
  m.addEventListener("pointermove", ev => { ev.stopPropagation(); showEvTip(e, ev); });
  m.addEventListener("pointerleave", hideTip);
  m.addEventListener("click", ev => { ev.stopPropagation(); hideTip(); openEvent(e); });
  m.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openEvent(e); } });
  return m;
}
CHART_LAYERS.push({
  name: "Journal", short: "Journal",
  space: () => jInWindow().length ? 18 * ckK() : 0,
  rows: () => jInWindow().map(e => [e.ts, "Journal", jSay(e.j), () => openEvent(e), "journal"]),
  draw(g, x, top, R, L) {
    const y = top + 9 * ckK();
    for (const e of jInWindow()) {
      const mx = x(e.ts); if (mx < L - 6 || mx > R + 4) continue;
      g.push(jTrendsMark(jShape(e.j, mx, y, {class: "jmark", fill: "var(--c-journal)", "fill-opacity": jRated(e.j) ? .25 + e.j.severity * .065 : .85,
        stroke: "var(--c-journal)", "stroke-width": 1.5, tabindex: 0, role: "button", "aria-label": jSay(e.j), "data-jid": e.id}), e));
    }
  },
}, {
  name: "Mood", short: "Mood",
  space: () => jInWindow("mood").length ? 20 * ckK() : 0,
  rows: () => jInWindow("mood").map(e => [e.ts, "Mood and energy", jSay(e.j), () => openEvent(e), "mood"]),
  draw(g, x, top, R, L) {
    for (const e of jInWindow("mood")) {
      const mx = x(e.ts); if (mx < L - 6 || mx > R + 4) continue;
      g.push(jTrendsMark(jMoodBars(e.j, mx, top + 20 * ckK() - 2, 13, {class: "jmood", tabindex: 0, role: "button", "aria-label": jSay(e.j), "data-jid": e.id}), e));
    }
  },
});
{
  const marks = $("#optMarks")?.closest("label");
  if (marks) {
    const box = el("input", {type: "checkbox", id: "optJournal", checked: jOnTrends || null, onchange: ev => {
      jOnTrends = ev.target.checked; try { localStorage.setItem("sg-journal-trends", jOnTrends ? "1" : "0"); } catch (e) {} renderCharts(); }});
    marks.after(el("label", {id: "optJournalLabel", class: "toggle"}, box, " Journal"));
  }
  // the Legend's keys, each shape kept with its word (#355), shown while there's an entry of the kind on the charts (#301)
  const legend = document.querySelector("#view-labs .legend"), ink = {fill: "var(--c-journal)", "fill-opacity": .6, stroke: "var(--c-journal)"};
  if (legend) legend.append(el("span", {class: "jlegend keys", "data-legend": "journal", hidden: true}, "Journal:",
    ...J_KINDS.filter(([k]) => k !== "mood").map(([k, n]) => el("span", {}, shapeKey(KIND_SHAPE[k], ink, 3.6), n.toLowerCase())), "bigger = more severe"),
    el("span", {class: "jlegend", "data-legend": "mood", hidden: true}, el("svg:svg", {viewBox: "0 0 14 14", width: 14, height: 14, "aria-hidden": "true"}, jMoodBars({mood: 8, energy: 5}, 7, 13.5, 11)),
      "Mood: filled bar mood, outlined bar energy; taller = higher"));
  LEGEND_HAS.journal = () => jInWindow().length > 0;
  LEGEND_HAS.mood = () => jInWindow("mood").length > 0;
}

/* ---------------------------------------------------------------- the drawer: one entry, the list, the form */
const jCanWrite = () => !!PROFILE && (PROFILE.role === "owner" || PROFILE.role === "editor");
const jPhotoUrl = p => `api/profiles/${PROFILE.id}/journal/photos/${p.id}`;
const jDrawer = (title, sub, k = "journal") => drawerHead(k, title, sub);   // drawer.js: the one layout (#270)
function jSeverityMeter(n, what = "Severity") {
  return el("span", {class: "jsev", role: "img", "aria-label": `${what} ${n} out of 10`},
    el("span", {class: "jsev-bar", "aria-hidden": "true"}, el("span", {style: {width: n * 10 + "%"}})), el("b", {text: `${n}/10`}));
}
/* a small outline with the entry's places filled, beside their names (which say it all; the picture is extra) */
const jWhereFigure = ids => el("div", {class: "jbfigs jbmini", "aria-hidden": "true"},
  ...["f", "b"].map(v => jbOutline(v, (p, id) => { if (ids.includes(id)) p.classList.add("on"); })));
function openJournal(e) {
  const j = e.j;
  state.selEv = e.id;
  const body = jDrawer(jTitle(j), `${j.kind === "mood" ? "Mood and energy" : J_LABEL[j.kind]} · ${jWhen(j)}`, e.k);   // its kind and when, said there only
  body.append(el("dl", {class: "kv"},
    jRated(j) ? [el("dt", {text: "Severity"}), el("dd", {}, jSeverityMeter(j.severity))] : null,
    j.mood != null ? [el("dt", {text: "Mood"}), el("dd", {}, jSeverityMeter(j.mood, "Mood"))] : null,
    j.energy != null ? [el("dt", {text: "Energy"}), el("dd", {}, jSeverityMeter(j.energy, "Energy"))] : null,
    j.where?.length ? [el("dt", {text: "Where"}), el("dd", {text: jWhere(j)})] : null,
    j.group ? [el("dt", {text: "Logged with"}), el("dd", {text: `the group “${j.group}”`})] : null,
    j.med ? [el("dt", {text: "Medication"}), el("dd", {text: j.med})] : null,
    el("dt", {text: "Written by"}), el("dd", {text: `${j.by || "someone"} · ${fmtDT(j.at * 1000)}` + (j.changed ? ` (changed${j.changed_by ? " by " + j.changed_by : ""} ${fmtDT(j.changed * 1000)})` : "")})));
  if (j.where?.length) body.append(jWhereFigure(j.where));
  if (j.note) body.append(el("div", {class: "sect", text: j.kind === "note" ? "Note" : "Notes"}), el("p", {class: "jnote", text: j.note}));
  if (j.photos?.length) body.append(el("div", {class: "sect", text: j.photos.length > 1 ? `Photos (${j.photos.length})` : "Photo"}),
    el("div", {class: "jpics"}, ...j.photos.map((p, i) => el("figure", {class: "jpic"},
      el("a", {href: jPhotoUrl(p), target: "_blank", rel: "noopener", "data-tip": "Open it full size"},
        el("img", {src: jPhotoUrl(p), alt: p.alt || `Photo ${i + 1} of ${j.photos.length} for ${jTitle(j)}`, loading: "lazy"})),
      p.alt ? el("figcaption", {text: p.alt}) : null))));
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  const acts = el("div", {class: "btns dgo"});
  if (jCanWrite()) drawerMenu([{text: "Edit", run: () => journalForm(j, {}, () => openEvent(e))},   // in the ⋯ menu, as a record's corrections are (#270)
    {text: "Delete", run: async () => {   // ask()ed: it can't be put back, photos and all (#363)
      if (!await ask({title: `Delete “${jTitle(j)}”?`, message: (j.photos?.length ? `The entry and its ${j.photos.length > 1 ? j.photos.length + " photos" : "photo"} go.` : "The entry goes.") + " This can't be undone.",
        action: "Delete", cancel: "Keep it", danger: true})) return;
      jSend(`/${j.id}`, {method: "DELETE"}, msg, {list: true});
    }}], "Change this entry");
  acts.append(el("button", {class: "btn", type: "button", text: "Day on the timeline", onclick: () => { setRange(e.ts - 15 * DAY, e.ts + 15 * DAY); pickTab("timeline"); }}),
    el("button", {class: "btn", type: "button", text: "All journal entries", onclick: () => journalHome(true)}));
  body.append(acts, msg);
  showDrawer();
  renderTimeline();
}

/* ---------------------------------------------------------------- the list, and the journal's home (#173)
   Its home is a card in the Timeline tab's Journal view (#tlJournal, timeline.js: #277), made here: the newest
   entries, with the filters and search, then where symptoms have been (#161). The panel shows the same list only when the Timeline tab is hidden (Settings → Tabs, #40). The card is built again, in place,
   whenever the journal changes (store.js, #271). */
const jFilter = {kinds: new Set(J_KINDS.map(([k]) => k)), med: "", q: "", inRange: false, all: false, where: ""};   // kinds: those shown
const J_FEW = 5;   // the card shows the newest five until "Show all"
const JOURNAL_HINT = "What you write is kept with the records, not with any import: undoing an import leaves it. It's in “Export everything” and in backups, photos included.";
const jMatch = e => jFilter.kinds.has(e.j.kind) && (!jFilter.med || e.j.med === jFilter.med) && (!jFilter.where || (e.j.where || []).includes(jFilter.where)) &&
  (!jFilter.q || e._q.includes(jFilter.q)) && (!jFilter.inRange || e.ts >= state.from && e.ts <= state.to);
const jIcon = e => el("svg:svg", {class: "jicon", viewBox: "-1 -1 20 20", width: 18, height: 18, "aria-hidden": "true"},
  e.j.kind === "mood" ? jMoodBars(e.j, 9, 17, 14) : jShape(e.j, 9, 9, {fill: "var(--c-journal)", "fill-opacity": jRated(e.j) ? .25 + e.j.severity * .065 : .85, stroke: "var(--c-journal)"}));
const jRow = e => el("button", {class: "jrow", type: "button", role: "listitem", onclick: () => openEvent(e), "aria-label": jSay(e.j)},
  el("span", {class: "d", text: fmtD(e.ts)}), jIcon(e),
  el("span", {class: "t"}, el("b", {text: e.title}), el("span", {class: "s", text: [e.j.kind === "mood" ? "Mood and energy" : J_LABEL[e.j.kind], e.j.med, e.j.group, jWhere(e.j), jPhotos(e.j)].filter(Boolean).join(" · ")})),
  jRated(e.j) ? el("span", {class: "badge", text: `${e.j.severity}/10`}) : el("span"));
/* the filters, the count and the entries, newest first; few: only the newest J_FEW until "Show all". -> {nodes, draw} */
function jList(few) {
  const meds = [...new Set(JOURNAL.map(e => e.j.med).filter(Boolean))].sort();
  // the kinds, and "Only the dates shown", as on/off toggle chips (#193, app.css .toggle)
  const kind = el("div", {class: "chips", role: "group", "aria-label": "Kinds of entry shown"}, ...J_KINDS.map(([k, t]) => {
    const b = el("button", {type: "button", class: "toggle", "data-kind": k, "aria-pressed": jFilter.kinds.has(k), onclick: () => {
      jFilter.kinds.has(k) ? jFilter.kinds.delete(k) : jFilter.kinds.add(k); b.setAttribute("aria-pressed", jFilter.kinds.has(k)); draw(); }},
      t + "s ", el("span", {class: "n", text: JOURNAL.filter(e => e.j.kind === k).length}));
    b.style.setProperty("--tone", "var(--c-journal)");
    return b;
  }));
  const med = el("select", {"aria-label": "Medication", hidden: !meds.length || null}, el("option", {value: "", text: "Any medication"}), ...meds.map(m => el("option", {value: m, text: m})));
  const q = el("input", {type: "search", placeholder: "Search the journal…", "aria-label": "Search the journal"});
  const shown = el("label", {class: "jinrange toggle"}, el("input", {type: "checkbox", checked: jFilter.inRange || null}), " Only the dates shown");
  med.value = jFilter.med; q.value = jFilter.q;
  const list = el("div", {class: "jlist", role: "list"}), count = el("p", {class: "hint", role: "status"}), sum = el("p", {class: "fsum", hidden: ""});
  const reset = () => {   // Reset filters (#190, filters.js): every kind, any medication and place, no search, any date
    jFilter.kinds = new Set(J_KINDS.map(([k]) => k)); jFilter.where = ""; med.value = ""; q.value = ""; shown.firstChild.checked = false;
    for (const b of kind.children) b.setAttribute("aria-pressed", "true");
    draw();
  };
  const more = el("button", {class: "btn jmore", type: "button", hidden: "", onclick: () => { jFilter.all = !jFilter.all; draw(); }});
  const draw = () => {
    Object.assign(jFilter, {med: med.value, q: q.value.trim().toLowerCase(), inRange: shown.firstChild.checked});
    const v = JOURNAL.filter(jMatch).sort((a, b) => b.ts - a.ts), cut = few && !jFilter.all && v.length > J_FEW;
    const filtered = jFilter.kinds.size < J_KINDS.length || jFilter.med || jFilter.q || jFilter.inRange || jFilter.where;
    filterLine(filtered ? showingParts(jFilter.kinds.size < J_KINDS.length && kindsShown(J_KINDS.filter(([k]) => jFilter.kinds.has(k)).map(([, t]) => t + "s"), J_KINDS.length),
      "entries", [jFilter.med && `for ${jFilter.med}`, jFilter.where && `about the ${lcLabel(jbLabel(jFilter.where))}`, jFilter.q && `matching “${q.value.trim()}”`, jFilter.inRange && "only on the dates shown"]) : [], reset, () => q, sum);
    count.textContent = !JOURNAL.length ? "" : cut ? `The newest ${J_FEW} of ${v.length}${filtered ? " that match" : ""}` : `${v.length} of ${JOURNAL.length} shown`;
    list.replaceChildren(...(cut ? v.slice(0, J_FEW) : v).map(jRow));
    if (!v.length) list.append(el("div", {class: "empty", text: JOURNAL.length ? "No entries match." : jCanWrite() ? "Add a note, a symptom or a side effect to start the journal." : "No one has written in this journal yet."}));
    more.hidden = !few || v.length <= J_FEW;
    more.textContent = jFilter.all ? "Show fewer" : `Show all ${v.length}`;
  };
  med.onchange = draw;
  q.oninput = draw; shown.firstChild.onchange = draw;
  draw();
  return {nodes: [JOURNAL.length ? el("div", {class: "jfilters"}, kind, med, q, shown) : null, sum, count, list, more], draw};
}
/* (#161) where symptoms and side effects have been, in the dates the timeline shows: the body map with each place's
   count, or the same as a table (#60). A place with entries is a button: the list above shows just those. */
function jWhereSection(showOnly) {
  const box = el("section", {class: "jwhere-map", "aria-labelledby": "jWhereTitle"});
  const draw = () => {
    const inDates = JOURNAL.filter(e => e.j.where?.length && e.ts >= state.from && e.ts <= state.to);
    const counts = {};
    for (const e of inDates) for (const id of e.j.where) {
      const c = counts[id] || (counts[id] = {n: 0, worst: null, last: 0});
      c.n++; if (jRated(e.j)) c.worst = Math.max(c.worst ?? 0, e.j.severity); c.last = Math.max(c.last, e.ts);
    }
    const table = state.tables.has("#journal-where");
    const rows = JB_ORDER.filter(id => counts[id]).sort((a, b) => counts[b].n - counts[a].n || JB_ORDER.indexOf(a) - JB_ORDER.indexOf(b));
    box.replaceChildren(...present(
      el("div", {class: "list-hd"}, el("h3", {id: "jWhereTitle", text: "Where symptoms have been"}), el("span", {class: "spacer"}),
        rows.length ? viewSeg("#journal-where", "where symptoms have been", draw, "Body map") : null),
      el("p", {class: "hint", text: `${fmtD(state.from)} – ${fmtD(state.to)}, the dates the timeline shows: ` + (rows.length
        ? `${inDates.length === 1 ? "1 symptom or side effect says" : inDates.length + " symptoms and side effects say"} where on the body. The number in each place is how many name it.`
        : "no symptom or side effect with a place on the body. Change the dates above to look further back.")}),
      !rows.length ? null : table
        ? chartTable(["Where", "Entries", "Worst severity", "Latest"], rows.map(id => [jbLabel(id), String(counts[id].n), counts[id].worst == null ? "" : `${counts[id].worst}/10`, fmtD(counts[id].last)]))
        : jBodyMap(counts, id => { showOnly(id); })));
  };
  draw();
  return {node: box, draw};
}
/* the card in the Timeline tab's Journal view: none for someone who can only read a journal no one has written in */
let jHomeDraw = null;
function journalHomeCard() {
  const home = $("#tlJournal");
  $("#journalHome")?.remove(); jHomeDraw = null;
  if (!home || !JOURNAL.length && !jCanWrite()) return;
  const n = JOURNAL.length, groups = jGroups();
  const card = el("section", {class: "card jhome", id: "journalHome", tabindex: -1, "aria-labelledby": "journalHomeTitle"},
    el("div", {class: "list-hd"}, el("h2", {id: "journalHomeTitle", text: n ? `Journal (${n})` : "Journal"}), el("span", {class: "spacer"}),
      jCanWrite() ? el("button", {class: "btn primary", type: "button", text: "New entry", onclick: () => journalNew()}) : null),
    el("p", {class: "hint", text: n ? "Notes, symptoms, side effects and moods written by the people who use this profile."
      : "Nothing written yet: the notes, symptoms, side effects and moods you write appear here and on the timeline."}),   // New entry, above (#199)
    jCanWrite() || groups.length ? el("div", {class: "btns jgroup-btns"},   // #161: symptom groups, logged in one go
      jCanWrite() && groups.length ? el("button", {class: "btn", type: "button", text: "Log a symptom group", onclick: () => journalPickGroup(closeDrawer)}) : null,
      el("button", {class: "btn", type: "button", text: groups.length ? `Symptom groups (${groups.length})` : "Symptom groups", onclick: () => journalGroups(closeDrawer)})) : null,
    aboutWords(JOURNAL_HINT, "Photos have where they were taken (GPS), the camera and the time taken out before they're kept. They're only in a share link when you tick “Photos in the journal”."));
  if (n) {
    const v = jList(true);
    card.append(...v.nodes.filter(Boolean));
    let map = null;
    if (JOURNAL.some(e => e.j.where?.length)) {
      map = jWhereSection(id => { jFilter.where = id; jFilter.inRange = true; card.querySelector(".jinrange input").checked = true; v.draw();
        card.querySelector(".jlist .jrow, .jlist .empty")?.scrollIntoView?.({block: "nearest"}); card.querySelector(".jlist .jrow")?.focus(); });
      card.append(map.node);
    }
    jHomeDraw = () => { v.draw(); map?.draw(); };
  }
  home.append(card);
}
storeOn("journal", () => keepFocus(journalHomeCard));
// "Only the dates shown" and the body map follow the period: drawn again with the tab (renderTimelineTab, timeline.js)
/* to the journal's home: the Timeline tab's Journal view, or the list in the panel when that tab is hidden. push:
   someone asked for it, so Back returns to the tab before (#208) */
function journalHome(push) {
  const card = $("#journalHome");
  if (!card || !shownTabs().includes("timeline")) return openJournalList();
  if (drawer.classList.contains("open")) closeDrawer();
  switchTab("timeline", "journal", push);
  window.scrollTo({top: 0, left: 0, behavior: "instant"}); card.focus({preventScroll: true});
}
function openJournalList() {
  state.selEv = null;
  const body = jDrawer("Journal", JOURNAL.length ? `${JOURNAL.length} entr${JOURNAL.length === 1 ? "y" : "ies"} written by the people who use this profile` : "Nothing written yet");
  body.append(...[el("div", {class: "btns"}, jCanWrite() ? el("button", {class: "btn primary", type: "button", text: "Add an entry", onclick: () => journalForm(null, {}, openJournalList)}) : null,
      jCanWrite() && jGroups().length ? el("button", {class: "btn", type: "button", text: "Log a symptom group", onclick: () => journalPickGroup(openJournalList)}) : null,
      aboutWords({title: "the journal"}, JOURNAL_HINT)),   // its ⓘ (#280)
    ...jList(false).nodes].filter(Boolean));   // append(null) would write "null"
  showDrawer();
  renderTimeline();
}
/* a new entry, in the panel over whatever is on screen; Cancel closes it */
function journalNew() {
  if (!jCanWrite()) return journalHome();
  journalForm(null, {}, closeDrawer);
}

/* ---------------------------------------------------------------- the form's parts */
const jUid = () => Math.random().toString(36).slice(2, 8);
const jRow2 = (lbl, input, cls) => el("div", {class: "jfield" + (cls ? " " + cls : "")}, el("label", {for: input.id, text: lbl}), input);
/* a 0-10 slider with its words: what: "Severity" | "Mood" | "Energy"; v: the value; aria: its accessible name if no
   <label for> names it */
function jScale(id, what, v, aria) {
  const words = what === "Severity" ? n => n === 0 ? "none" : n <= 3 ? "mild" : n <= 6 ? "moderate" : n <= 9 ? "severe" : "worst"
    : n => n <= 2 ? "very low" : n <= 4 ? "low" : n <= 6 ? "middling" : n <= 8 ? "high" : "very high";
  const input = el("input", {type: "range", id, min: 0, max: 10, step: 1, "aria-label": aria || null});
  input.value = Number.isInteger(v) ? v : 5;
  const out = el("output", {for: id, class: "jsev-out"});
  const say = () => { const n = +input.value; out.textContent = `${n}/10 · ${words(n)}`; input.setAttribute("aria-valuetext", out.textContent); };
  input.addEventListener("input", say); say();
  return {input, out, box: el("div", {class: "jsev-in"}, input, out)};
}
/* where on the body, behind a toggle that says what's chosen (journal-body.js draws the outline and the list) */
function jWherePart(chosen, label = "Where on the body (optional)") {
  let now = chosen || [];
  const sum = el("span", {class: "jwhere-sum"});
  const say = ids => { now = ids; sum.textContent = ids.length ? ": " + jbSay(ids) : ""; };
  const pick = jBodyPicker(now, say);
  const part = el("details", {class: "jwhere", open: now.length || null}, el("summary", {}, label, sum), pick.node);
  say(pick.get());
  return {node: part, get: () => pick.get()};
}
/* photos for an entry (#161): each sent as it's chosen, its metadata taken out by the server, shown here from the
   file itself (a data: address, as the page's policy allows) until the entry is saved. -> {node, get()} */
const J_PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"], J_PHOTOS_MAX = 6, J_PHOTO_MB = 20;
function jPhotosPart(have, uid) {
  const photos = (have || []).map(p => ({id: p.id, alt: p.alt || "", src: jPhotoUrl(p)}));
  const list = el("div", {class: "jpics jpics-edit"}), msg = el("p", {class: "hint jphoto-msg", role: "status"});
  const input = el("input", {type: "file", id: `j-photo-${uid}`, accept: J_PHOTO_TYPES.join(","), multiple: true, class: "jphoto-in",
    "aria-labelledby": `j-photo-${uid}-l j-photo-${uid}-b`});   // "Photos (optional) …, Add photos…"
  const draw = () => {
    list.replaceChildren(...photos.map((p, i) => {
      const alt = el("input", {type: "text", id: `j-alt-${uid}-${i}`, maxlength: 300, placeholder: "e.g. Red patch, 3 cm, left forearm", autocomplete: "off"});
      alt.value = p.alt; alt.oninput = () => { p.alt = alt.value; };
      // which photo it is, a field of the form: one removed or another put in its place is a change (drawer.js, #811)
      return el("figure", {class: "jpic"}, el("input", {type: "hidden", name: "photo", value: p.id}), el("img", {src: p.src, alt: p.alt || `Photo ${i + 1}`}),
        el("figcaption", {}, el("label", {for: alt.id, text: `What photo ${i + 1} shows (optional)`}), alt,
          el("button", {class: "btn", type: "button", text: "Remove", "aria-label": `Remove photo ${i + 1}`, onclick: () => {
            photos.splice(i, 1); draw(); sayIn(msg, "Photo removed. It's deleted once the entry is saved."); input.focus(); }})));
    }));
    input.disabled = photos.length >= J_PHOTOS_MAX;
  };
  input.onchange = async () => {   // the box holds what was chosen until it's sent (a change while it's sent, #811), then empties
    const files = [...input.files];
    for (const f of files) {
      if (photos.length >= J_PHOTOS_MAX) { sayIn(msg, `An entry can have at most ${J_PHOTOS_MAX} photos.`, true); break; }
      if (f.type && !J_PHOTO_TYPES.includes(f.type)) { sayIn(msg, `${f.name} isn't a JPEG, PNG or WebP picture.`, true); continue; }
      if (f.size > J_PHOTO_MB * 1048576) { sayIn(msg, `${f.name} is too big: a photo can be at most ${J_PHOTO_MB} MB.`, true); continue; }
      sayIn(msg, `Sending ${f.name}…`);
      try {
        const r = await api(`profiles/${PROFILE.id}/journal/photos`, {method: "PUT", body: f, headers: {"Content-Type": f.type || "application/octet-stream"}});
        const j = await r.json().catch(() => ({}));
        if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't send ${f.name} (${r.status}).`, true); continue; }
        const src = await new Promise((ok, no) => { const rd = new FileReader(); rd.onload = () => ok(rd.result); rd.onerror = no; rd.readAsDataURL(f); });
        photos.push({id: j.id, alt: "", src});
        draw();
        sayIn(msg, `${f.name} added.` + (j.removed?.length ? ` Taken out of the file before it was kept: ${j.removed.join(", ")}.` : " There was nothing in it to take out, such as where it was taken."));
      } catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); }
    }
    input.value = "";
  };
  draw();
  const node = el("div", {class: "jfield jphotos"}, el("span", {id: `j-photo-${uid}-l`}, "Photos (optional) ", el("span", {class: "hint", text: `JPEG, PNG or WebP, up to ${J_PHOTOS_MAX}`})),
    el("label", {class: "btn pick", id: `j-photo-${uid}-b`}, "Add photos…", input), list, msg);   // the button's look (#353)
  return {node, get: () => photos.map(p => ({id: p.id, alt: p.alt.trim()}))};
}

/* the add / edit form. j: the entry (null: a new one); preset: {kind, t} for a new one; back(): what Cancel shows */
function journalForm(j, preset, back) {
  const isNew = !j, cur = j || {kind: preset.kind || "symptom", t: preset.t || isoDay(Date.now()), title: "", severity: 5, note: "", med: null};
  const whose = ME && ME.profiles.length > 1 ? ` · ${PROFILE.name}` : "";   // which profile's journal, when there's a choice
  const body = jDrawer(isNew ? "New journal entry" : "Edit journal entry", isNew ? "A note, a symptom, a side effect or a mood" + whose : `${J_LABEL[cur.kind]} · ${jWhen(cur)}`);
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  const uid = jUid(), id = n => `j-${n}-${uid}`;
  const kinds = el("div", {class: "seg jkinds", role: "radiogroup", "aria-label": "Kind of entry"});
  let kind = cur.kind;
  const date = el("input", {type: "date", id: id("date"), required: true, max: isoDay(Date.now() + 366 * DAY)}), time = el("input", {type: "time", id: id("time")});   // the date a year ahead at most (#479)
  date.value = cur.t.slice(0, 10); time.value = cur.t.length > 10 ? cur.t.slice(11, 16) : "";
  const title = el("input", {type: "text", id: id("title"), maxlength: 200, autocomplete: "off"}); title.value = cur.title || "";
  const sev = jScale(id("sev"), "Severity", cur.severity);
  const medNames = [...new Set(EV.filter(e => e.k === "med" || e.k === "rx").map(e => e.title).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  if (cur.med && !medNames.includes(cur.med)) medNames.unshift(cur.med);   // linked before its import was undone: kept
  const med = el("select", {id: id("med")}, el("option", {value: "", text: medNames.length ? "None" : "No medications in the records"}), ...medNames.map(m => el("option", {value: m, text: m})));
  med.value = cur.med || "";
  const note = el("textarea", {id: id("note"), rows: 4, maxlength: 5000}); note.value = cur.note || "";
  const where = jWherePart(cur.where), photos = jPhotosPart(cur.photos, uid);
  // a mood (#161): mood and energy, each ticked in or left out
  const moodPart = (what, v) => {
    const s = jScale(id(what.toLowerCase()), what, v, `${what}, 0 lowest to 10 highest`);
    const on = el("input", {type: "checkbox", id: id(what.toLowerCase() + "-on"), checked: j ? v != null || null : true});
    const sync = () => { s.input.disabled = !on.checked; s.out.classList.toggle("off", !on.checked); };
    on.onchange = sync; sync();
    return {get: () => on.checked ? +s.input.value : null, node: el("div", {class: "jfield jscale"},
      el("label", {for: on.id}, on, ` ${what} `, el("span", {class: "hint", text: "0 lowest – 10 highest"})), s.box)};
  };
  const mood = moodPart("Mood", cur.mood), energy = moodPart("Energy", cur.energy);
  const groupLine = isNew && jGroups().length ? el("p", {class: "hint jgroup-line"}, "Several at once? ",
    el("button", {class: "linkish", type: "button", text: "Log a symptom group", onclick: () => journalPickGroup(() => journalForm(j, preset, back))})) : null;
  const rated = [groupLine, jRow2("What was it?", title), el("div", {class: "jfield"}, el("label", {for: sev.input.id}, "Severity ", el("span", {class: "hint", text: "0 none – 10 worst"})), sev.box),
    where.node, jRow2("Linked medication (optional)", med)].filter(Boolean);
  const moods = [mood.node, energy.node];
  const noteRow = jRow2("Note", note), timeRow = jRow2("Time (optional)", time);
  const setKind = k => {
    kind = k;
    for (const b of kinds.children) { b.setAttribute("aria-checked", b.dataset.kind === k); b.tabIndex = b.dataset.kind === k ? 0 : -1; }
    for (const r of rated) r.hidden = k === "note" || k === "mood";
    for (const r of moods) r.hidden = k !== "mood";
    photos.node.hidden = k === "mood"; timeRow.hidden = k === "mood";   // a mood is a day's, with no photos
    noteRow.querySelector("label").textContent = k === "note" ? "Note" : "Note (optional)";
    title.placeholder = k === "side_effect" ? "e.g. Nausea" : k === "symptom" ? "e.g. Headache" : "";
  };
  kinds.append(...J_KINDS.map(([k, t]) => el("button", {type: "button", role: "radio", "data-kind": k, text: t, onclick: () => setKind(k),
    onkeydown: ev => { const i = J_KINDS.findIndex(([x]) => x === kind), d = ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : ev.key === "ArrowLeft" || ev.key === "ArrowUp" ? -1 : 0;
      if (d) { ev.preventDefault(); const n = J_KINDS[(i + d + J_KINDS.length) % J_KINDS.length][0]; setKind(n); kinds.querySelector(`[data-kind="${n}"]`).focus(); } }})));
  setKind(kind);
  const form = el("form", {class: "jform", novalidate: true},
    el("div", {class: "jfield"}, el("span", {class: "jlabel", text: "Kind"}), kinds),
    el("div", {class: "jwhen"}, jRow2("Date", date), timeRow),
    ...rated, ...moods, noteRow, photos.node,
    el("div", {class: "btns"}, el("button", {class: "btn primary", type: "submit", text: isNew ? "Add to journal" : "Save"}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => back()})), msg);
  form.onsubmit = ev => {
    ev.preventDefault();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date.value)) { sayIn(msg, "Pick a date.", true); date.focus(); return; }
    if (date.value > date.max) { sayIn(msg, "That date is more than a year ahead: check the year.", true); date.focus(); return; }   // typed past the picker's max (#479)
    if (kind === "mood") {
      if (mood.get() == null && energy.get() == null) { sayIn(msg, "Tick Mood, Energy or both.", true); return; }
      const out = {kind, t: date.value, mood: mood.get(), energy: energy.get(), note: note.value};
      return jSend(isNew ? "" : `/${j.id}`, {method: isNew ? "POST" : "PUT", body: JSON.stringify(out)}, msg, isNew ? {created: true} : {entry: j.id});
    }
    const out = {kind, t: date.value + (time.value ? "T" + time.value.slice(0, 5) : ""), note: note.value, photos: photos.get()};
    if (kind !== "note") Object.assign(out, {title: title.value, severity: +sev.input.value, med: med.value || null, where: where.get(), group: j?.group || null});
    else if (j && j.title) out.title = j.title;
    jSend(isNew ? "" : `/${j.id}`, {method: isNew ? "POST" : "PUT", body: JSON.stringify(out)}, msg, isNew ? {created: true} : {entry: j.id});
  };
  body.append(form);
  showDrawer();
  (kind === "note" || kind === "mood" ? note : title).focus({preventScroll: true});
}

/* ---------------------------------------------------------------- symptom groups (#161)
   The person's own named sets of symptoms, kept as the profile setting journal.groups. Logged in one go: one symptom
   entry each, with its own severity (and place), the same date and note, each saying which group it was logged with. */
async function jStoreGroups(groups, msg) {
  sayIn(msg, "Saving…");
  try {
    const r = await api(`profiles/${PROFILE.id}/settings/journal.groups`, {method: "PUT", body: JSON.stringify({value: groups})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save the groups (${r.status}).`, true); return false; }
    applySettings(j);
    journalHomeCard();
    return true;
  } catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); return false; }
}
/* a deleted group's Undo (#367, as a Labs view's, #359): put back into the groups as they are now on the server, next
   to its old neighbours (before the group that followed it, or after the one before it, of those still there); one
   made since under its name is left as it is, said so. order: the groups' names it was among. */
async function jPutBack(g, order, back) {
  const r = await api(`profiles/${PROFILE.id}/settings`);
  if (!r.ok) throw new Error(`the server answered ${r.status}`);
  const j = await r.json(), now = Array.isArray(j.journal?.groups) ? j.journal.groups : [];
  const shown = () => drawer.classList.contains("open") && $("#dbody .jgroups-home");   // the groups, if they're still what the panel shows
  if (now.some(x => x.name.toLowerCase() === g.name.toLowerCase())) {
    applySettings(j);   // drawn as they are
    journalHomeCard();
    const text = `A group called “${g.name}” was made since: it's left as it is.`;
    if (shown()) journalGroups(back, text); else toast(text);
    return;
  }
  const i = order.indexOf(g.name), at = n => now.findIndex(x => x.name === n);
  const after = order.slice(i + 1).map(at).find(k => k >= 0), before = order.slice(0, i).reverse().map(at).find(k => k >= 0);
  const next = [...now];
  next.splice(after ?? (before == null ? next.length : before + 1), 0, g);
  if (!await jStoreGroups(next, shown() ? $("#dbody .edit-msg") : null)) throw new Error("it couldn't be saved");
  if (shown()) journalGroups(back, `Undone: “${g.name}” is back.`);
}
function journalGroups(back, said, quiet) {   // quiet: said in a toast, so only shown here (sayIn's say: false)
  state.selEv = null;
  const groups = jGroups(), w = jCanWrite();
  const body = jDrawer("Symptom groups", groups.length ? `${groups.length} group${groups.length > 1 ? "s" : ""} for this profile` : "None yet");
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  body.append(el("p", {class: "hint jgroups-home", text: "Your own sets of symptoms you often have together, such as “Migraine day”: headache, nausea, light sensitivity. Logging a group adds each symptom with its own severity, in one go."}),
    groups.length ? el("ul", {class: "jgroups"}, ...groups.map((g, i) => el("li", {},
      el("div", {class: "t"}, el("b", {text: g.name}), el("span", {class: "s", text: g.symptoms.join(", ")})),
      w ? el("div", {class: "btns"},
        el("button", {class: "btn primary", type: "button", text: "Log it", "aria-label": `Log ${g.name}`, onclick: () => journalGroupForm(g, () => journalGroups(back))}),
        el("button", {class: "btn", type: "button", text: "Edit", "aria-label": `Edit ${g.name}`, onclick: () => journalGroupEdit(i, () => journalGroups(back))}),
        el("button", {class: "btn", type: "button", text: "Delete", "aria-label": `Delete ${g.name}`, onclick: async () => {
          // at once, with an Undo that puts it back next to its old neighbours among the groups there are then (#363,
          // #367; it asked first before): the entries logged with it stay as they are either way
          const said = `Deleted “${g.name}”. The entries logged with it stay.`, order = groups.map(x => x.name);
          if (!await jStoreGroups(groups.filter((_, k) => k !== i), msg)) return;
          journalGroups(back, said, true);
          toast(said, {action: "Undo", onAction: () => jPutBack(g, order, back)});
        }})) : null))) : el("p", {class: "empty", text: w ? "No groups yet." : "No one has made a symptom group for this profile."}),
    el("div", {class: "btns"}, w ? el("button", {class: "btn primary", type: "button", text: "New group", onclick: () => journalGroupEdit(-1, () => journalGroups(back))}) : null,
      el("button", {class: "btn", type: "button", text: "Back", onclick: () => back()})), msg);
  showDrawer();
  if (said) sayIn(msg, said, false, {say: !quiet});
}
/* make (i -1) or change group i: a name and its symptoms, one a line */
function journalGroupEdit(i, back) {
  const groups = jGroups(), g = groups[i] || {name: "", symptoms: []}, uid = jUid();
  const body = jDrawer(i < 0 ? "New symptom group" : "Edit symptom group", i < 0 ? "A name and its symptoms" : g.name);
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  const name = el("input", {type: "text", id: `jg-name-${uid}`, maxlength: 80, autocomplete: "off", placeholder: "e.g. Migraine day"}); name.value = g.name;
  const syms = el("textarea", {id: `jg-syms-${uid}`, rows: 5, placeholder: "Headache\nNausea\nLight sensitivity"}); syms.value = g.symptoms.join("\n");
  const form = el("form", {class: "jform", novalidate: true}, jRow2("Name", name),
    el("div", {class: "jfield"}, el("label", {for: syms.id}, "Symptoms ", el("span", {class: "hint", text: "one a line, up to 12"})), syms),
    el("div", {class: "btns"}, el("button", {class: "btn primary", type: "submit", text: "Save group"}), el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => back()})), msg);
  form.onsubmit = async ev => {
    ev.preventDefault();
    const next = {name: name.value.trim(), symptoms: syms.value.split("\n").map(s => s.trim()).filter(Boolean)};
    if (!next.name) { sayIn(msg, "Give the group a name.", true); name.focus(); return; }
    if (!next.symptoms.length) { sayIn(msg, "List at least one symptom.", true); syms.focus(); return; }
    const all = groups.slice(); if (i < 0) all.push(next); else all[i] = next;
    if (await jStoreGroups(all, msg)) back();
  };
  body.append(form);
  showDrawer();
  name.focus({preventScroll: true});
}
/* which group to log: straight to its form when there's one */
function journalPickGroup(back) {
  const groups = jGroups();
  if (groups.length === 1) return journalGroupForm(groups[0], back);
  const body = jDrawer("Log a symptom group", "Which group?");
  body.append(el("ul", {class: "jgroups"}, ...groups.map(g => el("li", {},
    el("div", {class: "t"}, el("b", {text: g.name}), el("span", {class: "s", text: g.symptoms.join(", ")})),
    el("div", {class: "btns"}, el("button", {class: "btn primary", type: "button", text: "Log it", "aria-label": `Log ${g.name}`, onclick: () => journalGroupForm(g, () => journalPickGroup(back))}))))),
    el("div", {class: "btns"}, el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => back()})));
  showDrawer();
  body.querySelector("button")?.focus({preventScroll: true});
}
/* a group's form: one row a symptom, each ticked in (had it) with its own severity and, if you like, where */
function journalGroupForm(g, back) {
  const uid = jUid(), body = jDrawer("Log a symptom group", g.name);
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  const date = el("input", {type: "date", id: `jgf-date-${uid}`, required: true, max: isoDay(Date.now() + 366 * DAY)}), time = el("input", {type: "time", id: `jgf-time-${uid}`});
  date.value = isoDay(Date.now());
  const note = el("textarea", {id: `jgf-note-${uid}`, rows: 3, maxlength: 5000});
  const rows = g.symptoms.map((s, i) => {
    const had = el("input", {type: "checkbox", id: `jgf-had-${uid}-${i}`, checked: true});
    const sev = jScale(`jgf-sev-${uid}-${i}`, "Severity", 5, `${s}: severity, 0 none to 10 worst`);
    const where = jWherePart([], "Where (optional)");
    const rest = el("div", {class: "jgf-rest"}, sev.box, where.node);
    had.onchange = () => { rest.hidden = !had.checked; };
    return {s, had, sev, where, node: el("fieldset", {class: "jgf-row"}, el("legend", {class: "vh", text: s}),
      el("label", {for: had.id, class: "jgf-had"}, had, " ", el("b", {text: s})), rest)};
  });
  const form = el("form", {class: "jform", novalidate: true},
    el("p", {class: "hint", text: "Untick any you didn't have. Each is saved as a symptom of its own, with its own severity."}),
    el("div", {class: "jwhen"}, jRow2("Date", date), jRow2("Time (optional)", time)),
    ...rows.map(r => r.node), jRow2("Note for all of them (optional)", note),
    el("div", {class: "btns"}, el("button", {class: "btn primary", type: "submit", text: "Add to journal"}), el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => back()})), msg);
  form.onsubmit = ev => {
    ev.preventDefault();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date.value)) { sayIn(msg, "Pick a date.", true); date.focus(); return; }
    if (date.value > date.max) { sayIn(msg, "That date is more than a year ahead: check the year.", true); date.focus(); return; }   // typed past the picker's max (#479)
    const items = rows.filter(r => r.had.checked).map(r => ({title: r.s, severity: +r.sev.input.value, where: r.where.get()}));
    if (!items.length) { sayIn(msg, "Tick at least one symptom.", true); return; }
    jSend("/group", {method: "POST", body: JSON.stringify({group: g.name, t: date.value + (time.value ? "T" + time.value.slice(0, 5) : ""), note: note.value, items})}, msg, {list: true});
  };
  body.append(form);
  showDrawer();
  rows[0]?.sev.input.focus({preventScroll: true});
}

/* every change: the reply's entries put in the records in place (store.js, #271), which draws again what shows the
   journal, and what was being looked at opened again: the entry (reopen: {entry}, or {created} for a new one), else
   the journal's home ({list}) */
async function jSend(path, opts, msg, reopen) {
  sayIn(msg, "Saving…");
  let r, j;
  try { r = await api(`profiles/${PROFILE.id}/journal${path}`, opts); j = await r.json().catch(() => ({})); }
  catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); return; }
  if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save the entry (${r.status}).`, true); return; }
  // a profile with no records yet (#401): the page again, with the entry on the Timeline's Journal view (#472: the
  // empty card has no tabs to draw it in), the entry opened there and its saving said (#477: journalSaved)
  if (!DATA) {
    history.replaceState({journalSaved: reopen.created ? j.id : 0}, "", location.pathname + location.search + "#timeline-journal");
    location.reload(); return;
  }
  storePut("journal", j);
  const id = reopen.created ? j.id : reopen.entry;
  if (id && byId.has(-id)) openEvent(byId.get(-id)); else journalHome();
  announce(opts.method === "DELETE" ? "Entry deleted." : "Saved.");
}
/* the page again after the first entry on a profile with no records (jSend, #477): as a save with records does, the
   entry opened in the drawer, focus there (a group's entries: the journal's home), and a toast that it was saved */
function journalSaved() {
  const id = history.state?.journalSaved;
  if (id == null) return;
  try { history.replaceState(null, "", location.href); } catch (e) {}   // not again on the next reload
  if (id && byId.has(-id)) openEvent(byId.get(-id)); else journalHome();
  toast(id ? "Entry saved." : "Entries saved.");
}
/* arriving from Add → Journal entry on another page (nav.js links to ./#new-journal-entry) */
function journalResume() {
  if (location.hash !== "#new-journal-entry") return;
  history.replaceState(null, "", location.pathname + location.search + tabHash());   // a reload doesn't open it again; the tab's # (#208)
  journalNew();
}

/* in the drawer of any event: write a journal note dated to it */
function journalAddAction(e) {
  if (!jCanWrite() || !e || !isFinite(e.ts)) return el("span");
  const t = /T\d{2}:\d{2}/.test(e.t || "") ? isoDay(e.ts) + "T" + (z => `${String(z.getHours()).padStart(2, "0")}:${String(z.getMinutes()).padStart(2, "0")}`)(zdate(e.ts)) : isoDay(e.ts);
  return el("div", {class: "jadd"}, el("button", {class: "btn", type: "button", text: "Add a journal note for this day",
    "data-tip": "A note, symptom or side effect, dated to this event", onclick: () => journalForm(null, {kind: "note", t}, () => openEvent(e))}));
}
