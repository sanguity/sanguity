/* Sanguity · the visit summary (#48), on the Sharing page (#168): pick the visit and the "since" point, tick
   sections, add labs to trend, type questions (kept as your own draft for the profile as you type, #219), preview, download the PDF.
   The server builds both the preview and the PDF from the same records (server/summary.py); nothing here is put
   into innerHTML. Follows the page's profile picker (#profile, filled by sharing.js); call() is sharing.js's. */
"use strict";   // $, el and api come from util.js
let PID = null, OPTS = null, PICKED = [];

const fmtDay = d => d ? fmtD(dayTs(d)) : "";   // a day, in this person's date format (dates.js, #42)

/* ---------------------------------------------------------------- the choices */
function chosen() {
  const a = $("#appt").value, s = $("#since").value;
  return {appointment: a === "" ? null : +a, since: s || null, labs: PICKED,
    sections: [...document.querySelectorAll("#sections input:checked")].map(i => i.value), questions: $("#questions").value};
}

function drawPicked() {
  $("#picked").replaceChildren(...PICKED.map((n, i) => el("li", {}, labelOf(n),
    el("button", {class: "btn small", type: "button", text: "×", "aria-label": `Don't trend ${labelOf(n)}`,
      onclick: () => { PICKED.splice(i, 1); drawPicked(); }}))));
}
const labelOf = n => (OPTS.labs.find(l => l.name === n) || {label: n}).label;
function addLab() {
  const v = $("#labIn").value.trim();
  const hit = OPTS.labs.find(l => l.label.toLowerCase() === v.toLowerCase() || l.name.toLowerCase() === v.toLowerCase());
  if (!hit) { sayIn($("#buildMsg"), v ? `There's no lab called “${v}” in these records.` : "", true); return; }
  $("#labIn").value = ""; $("#labIn").focus();
  if (OPTS.keyLabs.includes(hit.name)) { sayIn($("#buildMsg"), `${hit.label} is a key lab, so it's trended already.`); return; }
  if (!PICKED.includes(hit.name)) { PICKED.push(hit.name); drawPicked(); }
  sayIn($("#buildMsg"), "");
}
$("#labAdd").onclick = addLab;
$("#labIn").addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); addLab(); } });

/* the questions draft, kept a moment after typing stops */
let qTimer = null, qSaved = "";
async function saveQuestions(pid = PID) {   // pid: the profile they were typed for, when switching away from it
  clearTimeout(qTimer); qTimer = null;
  const text = $("#questions").value;
  if (text === qSaved) return;
  try { await call(`profiles/${pid}/summary/questions`, "PUT", {text}); qSaved = text; sayIn($("#qMsg"), "Draft saved."); }
  catch (e) { sayIn($("#qMsg"), "The draft wasn't saved: " + e.message, true); }
}
$("#questions").addEventListener("input", () => { sayIn($("#qMsg"), ""); clearTimeout(qTimer); qTimer = setTimeout(saveQuestions, 800); });
$("#questions").addEventListener("blur", () => { if (qTimer) saveQuestions(); });

async function load() {
  $("#build").hidden = true; $("#preview").hidden = true; sayIn($("#loadMsg"), "Loading…");
  try { OPTS = await call(`profiles/${PID}/summary`); } catch (e) { sayIn($("#loadMsg"), e.message, true); return; }
  sayIn($("#loadMsg"), OPTS.has_records ? "" : `${OPTS.profile.name} has no records yet, so the summary will be mostly empty.`);
  $("#appt").replaceChildren(...OPTS.upcoming.map((u, i) => el("option", {value: i, text: `${u.title}, ${fmtDay(u.date)}${u.who ? " · " + u.who : ""}`})),
    el("option", {value: "", text: "No particular appointment"}));
  $("#appt").value = OPTS.upcoming.length ? "0" : "";
  const visits = OPTS.visits;
  $("#since").replaceChildren(...(visits.length
    ? [el("option", {value: "", text: `The last visit (${visits[0].title}, ${fmtDay(visits[0].date)})`}),
       ...visits.slice(1).map(v => el("option", {value: v.date, text: `${v.title}, ${fmtDay(v.date)}`}))]
    : [el("option", {value: "", text: "The last 90 days (no past visits)"})]));
  $("#whenHint").textContent = OPTS.upcoming.length ? "Upcoming appointments come from the records; the next one is chosen."
    : "There are no upcoming appointments in the records.";
  $("#sections").replaceChildren(...OPTS.sections.map(s => el("label", {class: "check"},
    el("input", {type: "checkbox", value: s.id, checked: !(s.ask || s.off) || null}), s.label,   // off: a measure, unticked (#225)
    s.ask ? el("span", {class: "muted", text: " (only if you tick it, each time)"}) : null)));   // cycle records (#183)
  $("#keyHint").textContent = OPTS.keyLabs.length
    ? `The key labs from Settings: ${OPTS.keyLabs.map(labelOf).join(", ")}. Add any others here.`
    : "No key labs with results (they're set in Settings). Add labs here to trend them.";
  $("#labNames").replaceChildren(...OPTS.labs.map(l => el("option", {value: l.label})));
  PICKED = []; drawPicked();
  $("#questions").value = qSaved = OPTS.questions || ""; sayIn($("#qMsg"), "");
  $("#notice").textContent = OPTS.notice;
  $("#build").hidden = false;
}

/* ---------------------------------------------------------------- preview */
const cell = (text, cls) => el("td", {class: cls, text: text ?? ""});
function table(heads, rows, cls = "") {   // a region that can be scrolled from the keyboard on a narrow screen (#225's wider tables)
  return scrollBox({class: "scroll", "aria-label": heads.filter(Boolean).join(", ")}, el("table", {class: ("tbl " + cls).trim()},
    el("thead", {}, el("tr", {}, ...heads.map(h => el("th", {text: h})))), el("tbody", {}, ...rows)));
}
function spark(pts) {
  const w = 70, h = 16, svg = el("svg:svg", {class: "spark", viewBox: `0 0 ${w} ${h}`, "aria-hidden": "true"});
  if (pts.length < 2) return svg;
  const ts = pts.map(p => zParse(p[0])), vs = pts.map(p => p[1]);
  let lo = Math.min(...vs), hi = Math.max(...vs); if (lo === hi) { lo -= 1; hi += 1; }
  const t0 = Math.min(...ts), t1 = Math.max(...ts);
  const x = t => 3 + (t - t0) / ((t1 - t0) || 1) * (w - 6), y = v => h - 3 - (v - lo) / (hi - lo) * (h - 6);
  svg.append(el("svg:path", {d: pts.map((p, i) => (i ? "L" : "M") + x(ts[i]).toFixed(1) + "," + y(p[1]).toFixed(1)).join(""),
    fill: "none", stroke: "var(--muted)", "stroke-width": 1.2}),
    ...pts.map((p, i) => el("svg:circle", {cx: x(ts[i]).toFixed(1), cy: y(p[1]).toFixed(1), r: p[2] ? 2 : 1.3, fill: p[2] ? "var(--crit)" : "var(--ink)"})));
  return svg;
}
/* a measures block's cell (#225): a day, or a local wall time, in this person's format; else its text */
const measureCell = (kind, v) => !v ? v : kind === "day" ? fmtDay(v) : kind === "time" ? fmtDT(zParse(v)) : v;
function renderPreview(s) {
  const box = $("#preview"), since = fmtDay(s.since.date);
  const labels = Object.fromEntries(OPTS.sections.map(x => [x.id, x.label]));
  const parts = [el("div", {class: "sum-head"},
    el("h2", {text: `Preview: ${s.pages} page${s.pages === 1 ? "" : "s"}`}),
    el("p", {}, el("b", {text: s.profile}), ` · prepared ${fmtDay(s.prepared)}`),
    el("p", {text: s.for ? `For: ${s.for.title} on ${fmtDay(s.for.date)}${s.for.who ? " · " + s.for.who : ""}` : "For: no particular appointment"}),
    el("p", {text: `Since: ${since}${s.since.label ? ` (${s.since.label})` : ""}`}),
    s.fits ? null : el("p", {class: "fitwarn", text: "Not everything fits on two pages; left out: " +
      Object.entries(s.left_out).map(([k, n]) => `${labels[k] || k}${n ? ` (${n})` : ""}`).join(", ") + ". Untick a section or shorten the questions."}))];
  const none = t => el("p", {class: "muted", text: t});
  for (const id of s.sections) {
    if (id === "out_of_range") {
      parts.push(el("h3", {text: `Out of range since ${since} (${s.out_of_range.length})`}));
      parts.push(s.out_of_range.length ? table(["Test", "Result", "Flag", "Range", "Date", "Source"], s.out_of_range.map(r => el("tr", {},
        cell(r.label + (r.of > 1 ? ` (${r.out} of ${r.of})` : "")), cell(`${r.value} ${r.unit}`.trim()), cell(r.flag, "flag"),
        cell(r.range || "not given"), cell(fmtDay(r.date)), cell(r.source)))) : none("No results since then are outside the reference range on their own report."));
    } else if (id === "trends") {
      parts.push(el("h3", {text: `Trends since ${since} (${s.trends.length})`}));
      parts.push(s.trends.length ? table(["Test", "Then", "Now", "Change", "Since", "", "Now's range"], s.trends.map(r => el("tr", {},
        cell(r.label + (r.unit ? ` (${r.unit})` : "")), cell(r.then ? `${r.then.value} · ${fmtDay(r.then.date)}` : "none before"),
        cell(r.now ? `${r.now.value}${r.now.flag ? ` ${{High: "▲", Low: "▼"}[r.now.flag] || "◆"} ${r.now.flag}` : ""} · ${fmtDay(r.now.date)}` : "no new result", r.now && r.now.flag ? "flag" : null),   // in words, not red alone (#60)
        cell(r.change || ""), cell(String(r.since)), el("td", {}, spark(r.spark)), cell(r.now ? r.now.range : "")))) : none("None of the key labs has results."));
    } else if (id === "medications") {
      parts.push(el("h3", {text: `Current medications (${s.medications.length})`}));
      parts.push(s.medications.length ? table(["Medication", "Instructions", "Since", "Source"], s.medications.map(r => el("tr", {},
        cell(r.name, "wrap"), cell(r.instructions, "wrap"), cell(fmtDay(r.since)), cell(r.source)))) : none("No current medications in the records."));
    } else if (id === "appointments") {
      parts.push(el("h3", {text: `Upcoming appointments (${s.appointments.length})`}));
      parts.push(s.appointments.length ? table(["Date", "Appointment", "With", "Where"], s.appointments.map(r => el("tr", {},
        cell(fmtDay(r.date), r.this ? "flag" : null), cell(r.title + (r.this ? " (this visit)" : "")), cell(r.who), cell(r.where)))) : none("No upcoming appointments in the records."));
    } else if (id === "questions") {
      parts.push(el("h3", {text: `My questions (${s.questions.length})`}));
      parts.push(s.questions.length ? el("ol", {}, ...s.questions.map(q => el("li", {text: q}))) : none("No questions written down."));
    } else if (s.measures && s.measures[id]) {   // the phone's and watch's measures (#225): lead lines, a table, a note
      const b = s.measures[id];
      parts.push(el("h3", {text: `${labels[id] || id}${b.history ? "" : ` since ${since}`} (${b.total})`}), ...b.lead.map(t => el("p", {text: t})));
      parts.push(b.rows.length ? table(b.cols.map(c => c[0]), b.rows.map(r => el("tr", {}, ...r.map((v, i) => cell(measureCell(b.cols[i][1], v))))), "measures") : none(b.empty));
      if (b.total > b.rows.length) parts.push(el("p", {class: "hint", text: `The latest ${b.rows.length} of ${b.total} are listed.`}));
      if (b.note) parts.push(el("p", {class: "hint", text: b.note}));
    } else {
      const ex = s.extra.find(x => x.id === id); if (!ex) continue;
      parts.push(el("h3", {text: `${labels[id] || id} (${ex.rows.length})`}));
      parts.push(el("ul", {}, ...ex.rows.map(r => el("li", {text: [fmtDay(r.when), r.title, r.text].filter(Boolean).join(" · ")}))));
    }
  }
  if (s.settings_note) parts.push(el("p", {class: "hint", id: "sumSettingsNote", text: s.settings_note}));   // #44
  parts.push(el("p", {class: "hint sum-notice", text: s.notice}));
  box.replaceChildren(...parts); box.hidden = false;
}
$("#previewBtn").onclick = async () => {
  const b = $("#previewBtn"); b.disabled = true; sayIn($("#buildMsg"), "Making the preview…");
  try { renderPreview(await call(`profiles/${PID}/summary/preview`, "POST", chosen())); sayIn($("#buildMsg"), ""); $("#preview").scrollIntoView({behavior: smoothly(), block: "start"}); }
  catch (e) { sayIn($("#buildMsg"), e.message, true); }
  finally { b.disabled = false; }
};

/* ---------------------------------------------------------------- download: the PDF is fetched, then saved from memory */
$("#build").onsubmit = async ev => {
  ev.preventDefault();
  const b = $("#download"); b.disabled = true; sayIn($("#buildMsg"), "Making the PDF…");
  try {
    const r = await api(`profiles/${PID}/summary/pdf`, {method: "POST", body: JSON.stringify(chosen())});
    if (r.status === 401) { location.replace("login.html"); return; }
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error((typeof j.detail === "string" && j.detail) || "Please check the choices."); }
    const blob = await r.blob();
    const name = (/filename="([^"]+)"/.exec(r.headers.get("content-disposition") || "") || [])[1] || "visit-summary.pdf";
    const url = URL.createObjectURL(blob), a = el("a", {href: url, download: name, hidden: true});
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    qSaved = $("#questions").value; clearTimeout(qTimer); qTimer = null;   // the server kept these questions as the draft
    sayIn($("#buildMsg"), `Downloaded ${name}.`);
  } catch (e) { sayIn($("#buildMsg"), e.message, true); }
  finally { b.disabled = false; }
};

/* ---------------------------------------------------------------- follow the profile picker */
/* sharing.js fills it once it knows who's signed in (and the dates' format, dates.js), then sets its value; a
   questions draft not saved yet is saved to the profile it was typed for before the next one loads */
(() => {
  const sel = $("#profile");
  const pick = async () => {
    const v = +sel.value, was = PID;
    if (!v || v === PID) return;
    PID = v;
    if (qTimer && was) await saveQuestions(was);
    $("#summaryIntro").hidden = false;
    load();
  };
  sel.addEventListener("change", pick);
  new MutationObserver(pick).observe(sel, {childList: true});
})();
