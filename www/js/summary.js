/* Sanguity · the visit summary (#48), on the Sharing page (#168): pick the visit and the "since" point, tick
   sections, add labs to trend, type questions (kept as your own draft for the profile, #219: saved with the group's
   bar as any change is, #272, or by downloading the PDF), download the PDF; and the measures the profile ticks at
   first (#402). The preview beside the choices (#288; under them on a narrower screen) is drawn again as each one
   changes.
   The server builds both the preview and the PDF from the same records (server/summary.py); nothing here is put
   into innerHTML. Follows the page's profile picker (#profile, filled by sharing.js); call() is sharing.js's. */
"use strict";   // $, el and api come from util.js
let PID = null, OPTS = null, PICKED = [];
const DAY = 864e5;   // (core.js's, which this page doesn't load: the kit's time axis counts in it, #302)

const fmtDay = d => d ? fmtD(dayTs(d)) : "";   // a day, in this person's date format (dates.js, #42)

/* ---------------------------------------------------------------- the choices */
function chosen() {
  const a = $("#appt").value, s = $("#since").value;
  return {appointment: a === "" ? null : +a, since: s || null, labs: PICKED,
    sections: [...document.querySelectorAll("#sections input:checked")].map(i => i.value), questions: $("#questions").value};
}

function drawPicked() {
  $("#picked").replaceChildren(...PICKED.map((n, i) => el("li", {},   // an action chip each (#353), as presets.js's chosen conditions
    el("button", {class: "chip", type: "button", "aria-label": `Don't trend ${labelOf(n)}`, onclick: () => {
      PICKED.splice(i, 1); drawPicked();
      ($("#picked").querySelectorAll("button")[Math.min(i, PICKED.length - 1)] || $("#labIn")).focus(); }}, labelOf(n), icon("x")))));
  previewSoon();
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

/* the questions draft (#272): changed and not saved, it's marked so, and #build's data-dirty puts it in the group's
   bar (sidebar.js), whose Save keeps it (summarySave) and Discard puts the saved one back (summaryDiscard); leaving
   the page or picking another profile asks first. The PDF keeps it too (the server does, as it makes one). */
let qSaved = "";
function qMark() {
  const dirty = $("#questions").value !== qSaved;
  $("#questions").closest("section").classList.toggle("set-changed", dirty);
  if (dirty) $("#build").setAttribute("data-dirty", 1); else $("#build").removeAttribute("data-dirty");
}
$("#questions").addEventListener("input", () => { sayIn($("#qMsg"), ""); qMark(); });
/* the bar's Save: a toast says it, with an Undo that puts the draft before back, if it's still the one saved here */
async function summarySave(msg, had) {
  const pid = PID, text = $("#questions").value, was = qSaved, name = $("#profile").selectedOptions[0]?.textContent.replace(/ \([^)]*\)$/, "");
  try { await call(`profiles/${pid}/summary/questions`, "PUT", {text}); }
  catch (e) { sayIn($("#qMsg"), e.message, true, {say: false}); sayIn(msg, "Your questions aren't saved: " + e.message, true); return; }
  if (PID === pid) { qSaved = text; qMark(); sayIn($("#qMsg"), "Saved.", false, {say: false}); if (had) $("#questions").focus({preventScroll: true}); }
  toast("Saved your questions.", {action: "Undo", onAction: async () => {
    const now = (await call(`profiles/${pid}/summary`)).questions || "";
    let said = "Your questions were changed since, so they're left as they are.";
    if (now === text) { await call(`profiles/${pid}/summary/questions`, "PUT", {text: was}); said = "Undone: your questions are as before."; }
    if (PID !== pid) { toast(`${name}: ${said}`); return; }   // another profile shown now (#367)
    if (now === text) { const kept = $("#questions").value !== qSaved; qSaved = was; if (!kept) { $("#questions").value = was; previewSoon(); } qMark(); }
    sayIn($("#qMsg"), said);
  }});
}
function summaryDiscard() {
  $("#questions").value = qSaved; qMark(); previewSoon();
  sayIn($("#qMsg"), "Changes discarded: back as saved.");
  $("#questions").focus();
}

async function load() {
  $("#build").hidden = true; $("#preview").hidden = true; pvSeq++; sayIn($("#loadMsg"), "Loading…");
  $("#prevBody").replaceChildren(); $("#preview").removeAttribute("data-stale"); sayIn($("#prevMsg"), "", false, {say: false});   // not another profile's
  try { OPTS = await call(`profiles/${PID}/summary`); } catch (e) { sayIn($("#loadMsg"), e.message, true); return; }
  sayIn($("#loadMsg"), OPTS.has_records ? "" : `${OPTS.profile.name} has no records yet, so the summary will be mostly empty.`);
  $("#appt").replaceChildren(...OPTS.upcoming.map((u, i) => el("option", {value: i, text: `${u.title}, ${fmtDay(u.date)}`})),   // with whom: in the preview's For line (#492: cut in the pane)
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
    ? "Your key labs are trended; add others here."   // (one line in the pane, #280, #288)
    : "No key labs have results; add labs here.";
  $("#labNames").replaceChildren(...OPTS.labs.map(l => el("option", {value: l.label})));
  PICKED = []; drawPicked();
  $("#questions").value = qSaved = OPTS.questions || ""; sayIn($("#qMsg"), ""); qMark();
  $("#notice").textContent = OPTS.notice;
  $("#build").hidden = false;
  previewSoon(0);
}

/* ---------------------------------------------------------------- preview */
const cell = (text, cls) => el("td", {class: cls}, ...keepDates(text ?? ""));   // each date kept whole as the cells wrap in the pane (#288)
/* a trend's value and its day, "14.3 · Aug 18, 2026"; in the narrow pane the day goes under the value, without the dot
   (#492: "14.3 ·" was left over its date) */
const atCell = (value, day, cls) => el("td", {class: cls}, value,
  el("span", {class: "sum-at"}, el("span", {class: "sum-dot", text: " · "}), el("span", {class: "nw", text: fmtDay(day)})));
/* a range kept whole, never broken at its dash (#514: "13.5– / 17.5" in the pane; summary.css makes room for it) */
const rangeCell = t => el("td", {}, el("span", {class: "nw", text: t || ""}));
function table(heads, rows, cls = "") {   // a region that can be scrolled from the keyboard on a narrow screen (#225's wider tables)
  return scrollBox({class: "scroll", "aria-label": heads.filter(Boolean).join(", ")}, el("table", {class: ("tbl " + cls).trim()},
    el("thead", {}, el("tr", {}, ...heads.map(h => el("th", {text: h})))), el("tbody", {}, ...rows)));
}
/* a trend's sparkline (chartkit.js ckSpark, #299): the server's [day, value, flag, low, high], oldest first */
const spark = pts => ckSpark(pts.map(([d, v, f, lo, hi]) => ckPt(zParse(d), v, lo, hi, f)), {r: 2});
/* a trend's chart (chartkit.js ckSolo, #302), as the PDF draws it under the trends' table, its name over it: of its
   spark's points (the last result on or before the "since" day and each after, or with none after the last few), two
   or more */
const SUM_FLAG = {High: "▲ High", Low: "▼ Low"};
function sumChart(r) {
  const pts = r.spark.map(([d, v, f, lo, hi]) => Object.assign(ckPt(zParse(d), v, lo, hi, f), {d})), name = r.label + (r.unit ? ` (${r.unit})` : "");
  return el("figure", {class: "sum-chart"}, el("figcaption", {text: name}), ckSolo(pts, {h: 64,
    label: `${name}: a chart of ${pts.length} results, ${fmtDay(pts[0].d)} to ${fmtDay(pts.at(-1).d)}. The table above lists the first and the latest.`,
    say: p => `${fmtDay(p.d)}: ${p[1]}${r.unit ? " " + r.unit : ""}${p[4] ? " " + (SUM_FLAG[p[4]] || "◆ " + p[4]) : ""}`}));
}
/* a measures block's cell (#225): a day, or a local wall time, in this person's format; else its text */
const measureCell = (kind, v) => !v ? v : kind === "day" ? fmtDay(v) : kind === "time" ? fmtDT(zParse(v)) : v;
function renderPreview(s) {
  const box = $("#preview"), since = fmtDay(s.since.date);
  const labels = Object.fromEntries(OPTS.sections.map(x => [x.id, x.label]));
  // its length beside its heading, said only when it changes (not as it's first drawn)
  sayIn($("#prevMsg"), `${s.pages} page${s.pages === 1 ? "" : "s"}${s.fits ? "" : ", not everything fits"}`, false, {repeat: false, say: !box.hidden});
  const parts = [el("div", {class: "sum-head"},
    el("p", {}, el("b", {text: s.profile}), ` · prepared ${fmtDay(s.prepared)}`),
    el("p", {text: s.for ? [`For: ${s.for.title} on ${fmtDay(s.for.date)}`, s.for.who, s.for.where].filter(Boolean).join(" · ") : "For: no particular appointment"}),   // its place too, as the PDF's (#540)
    el("p", {text: `Since: ${since}${s.since.label ? ` (${s.since.label})` : ""}`}),
    s.fits ? null : el("p", {class: "fitwarn", text: "Not everything fits on two pages; left out: " +
      Object.entries(s.left_out).map(([k, n]) => `${labels[k] || k}${n ? ` (${n})` : ""}`).join(", ") + ". Untick a section or shorten the questions."}))];
  const none = t => el("p", {class: "muted", text: t});
  for (const id of s.sections) {
    if (id === "out_of_range") {
      parts.push(el("h3", {text: `Out of range since ${since} (${s.out_of_range.length})`}));
      parts.push(s.out_of_range.length ? table(["Test", "Result", "Flag", "Range", "Date", "Source"], s.out_of_range.map(r => el("tr", {},
        cell(r.label + (r.of > 1 ? ` (${r.out} of ${r.of})` : "")), cell(`${r.value} ${r.unit}`.trim()), cell(r.flag, "flag"),
        rangeCell(r.range || "not given"), cell(fmtDay(r.date)), cell(r.source)))) : none("No results since then are outside the reference range on their own report."));
    } else if (id === "trends") {
      parts.push(el("h3", {text: `Trends since ${since} (${s.trends.length})`}));
      parts.push(s.trends.length ? table(["Test", "Then", "Now", "Change", "Since", "", "Now's range"], s.trends.map(r => el("tr", {},
        cell(r.label + (r.unit ? ` (${r.unit})` : "")), r.then ? atCell(r.then.value, r.then.date) : cell("none before"),
        r.now ? atCell(`${r.now.value}${r.now.flag ? ` ${{High: "▲", Low: "▼"}[r.now.flag] || "◆"} ${r.now.flag}` : ""}`, r.now.date, r.now.flag ? "flag" : null)   // in words, not red alone (#60)
          : cell("no new result"),
        cell(r.change || ""), cell(String(r.since)), el("td", {}, spark(r.spark)), rangeCell(r.now && r.now.range)))) : none("None of the key labs has results."));
      // their charts (#302), where the PDF has room for them (s.charts: else it's made without them)
      const drawn = s.charts ? s.trends.filter(r => r.spark.filter(p => p[1] != null).length > 1) : [];
      if (drawn.length) parts.push(el("div", {class: "sum-charts"}, ...drawn.map(sumChart)));
    } else if (id === "medications") {
      parts.push(el("h3", {text: `Current medications (${s.medications.length})`}));
      parts.push(s.medications.length ? table(["Medication", "Instructions", "Since", "Source"], s.medications.map(r => el("tr", {},
        cell(r.name, "wrap"), cell(r.instructions, "wrap"), cell(fmtDay(r.since)), cell(r.source)))) : none("No current medications in the records."));
    } else if (id === "appointments") {
      parts.push(el("h3", {text: `Upcoming appointments (${s.appointments.length})`}));
      // this visit's date in ink, as the others: red is for out of range only (#299)
      parts.push(s.appointments.length ? table(["Date", "Appointment", "With", "Where"], s.appointments.map(r => el("tr", {},
        cell(fmtDay(r.date)), cell(r.title + (r.this ? " (this visit)" : "")), cell(r.who), cell(r.where)))) : none("No upcoming appointments in the records."));
    } else if (id === "questions") {
      parts.push(el("h3", {text: `My questions (${s.questions.length})`}));
      parts.push(s.questions.length ? el("ol", {}, ...s.questions.map(q => el("li", {text: q}))) : none("No questions written down."));
    } else if (s.measures && s.measures[id]) {   // the phone's and watch's measures (#225): lead lines, a table, a note
      // "1,381", as the lead writes it (#531); the server's text as it is (#540: a typed "120,135,140" was rewritten)
      const b = s.measures[id], n = b.total.toLocaleString("en-US");
      parts.push(el("h3", {text: `${labels[id] || id}${b.history ? "" : ` since ${since}`} (${n})`}), ...b.lead.map(t => el("p", {text: t})));
      parts.push(b.rows.length ? table(b.cols.map(c => c[0]), b.rows.map(r => el("tr", {}, ...r.map((v, i) => cell(measureCell(b.cols[i][1], v))))), "measures") : none(b.empty));
      if (b.total > b.rows.length) parts.push(el("p", {class: "hint", text: `The latest ${b.rows.length} of ${n} are listed.`}));
      if (b.note) parts.push(el("p", {class: "hint", text: b.note}));
    } else {
      const ex = s.extra.find(x => x.id === id); if (!ex) continue;
      parts.push(el("h3", {text: `${labels[id] || id} (${ex.rows.length})`}));
      parts.push(el("ul", {}, ...ex.rows.map(r => el("li", {text: [fmtDay(r.when), r.title, r.text].filter(Boolean).join(" · ")}))));
    }
  }
  if (s.settings_note) parts.push(el("p", {class: "hint", id: "sumSettingsNote", text: s.settings_note}));   // #44
  parts.push(el("p", {class: "hint sum-notice", text: s.notice}));
  for (const c of $("#prevBody").querySelectorAll(".ck-solo")) ckSoloRO.unobserve(c);   // (the charts drawn before)
  $("#prevBody").replaceChildren(...parts); box.removeAttribute("data-stale"); box.hidden = false;
  pvSight();
}
/* a preview that couldn't be drawn (#492): the pane is shown with what went wrong, the first time too (its message is
   inside it, and was hidden with it); one drawn before stays, dimmed and marked as not up to date */
function previewFailed(e) {
  const box = $("#preview"), had = $("#prevBody").childElementCount > 0;
  box.toggleAttribute("data-stale", had);
  sayIn($("#prevMsg"), had ? `Not up to date: ${e.message}` : `No preview: ${e.message}`, true);
  box.hidden = false;
  pvSight();
}
/* a tick below the preview (#571): the preview drawn again moved its box down, off the screen or under the toasts, and
   nothing brought it back (sgFoot does only as its layer grows, not as a fourth toast takes the first's place); so
   once the toasts are measured, and again once the preview's drawn, the box with focus is brought clear of them (the
   page's scroll padding), a frame later: after the browser's own scroll anchoring has moved the page */
let pvKeep = "";
const inSight = id => { sgFoot(); requestAnimationFrame(() => { const a = document.activeElement; if (a?.id === id) a.scrollIntoView({block: "nearest"}); }); };
function pvSight() { if (pvKeep) inSight(pvKeep); pvKeep = ""; }
/* the live preview (#288): any choice changed asks for it again, a moment later (a while longer as questions are
   typed), only the last asked for drawn; meanwhile it's marked busy (aria-busy, dimmed). A lab being typed in isn't
   a choice until it's added. */
let pvSeq = 0, pvTimer = 0;
function previewSoon(wait = 300) {
  if (!OPTS || $("#build").hidden) return;
  const n = ++pvSeq;
  $("#preview").setAttribute("aria-busy", "true");
  clearTimeout(pvTimer);
  pvTimer = setTimeout(async () => {
    const pid = PID;
    try { const s = await call(`profiles/${pid}/summary/preview`, "POST", chosen()); if (n === pvSeq && pid === PID) renderPreview(s); }
    catch (e) { if (n === pvSeq && pid === PID) previewFailed(e); }
    finally { if (n === pvSeq) $("#preview").removeAttribute("aria-busy"); }
  }, wait);
}
$("#build").addEventListener("change", ev => { if (!["labIn", "questions"].includes(ev.target.id)) previewSoon(); });
$("#build").addEventListener("input", ev => { if (ev.target.id === "questions") previewSoon(700); });

/* ---------------------------------------------------------------- download: the PDF is fetched, then saved from memory */
$("#build").onsubmit = async ev => {
  ev.preventDefault();
  // not disabled while it's made (aria-disabled, as a toast's Undo): focus stays on it (#571: it went to the page)
  const b = $("#download");
  if (b.getAttribute("aria-disabled") === "true") return;
  b.setAttribute("aria-disabled", "true"); sayIn($("#buildMsg"), "Making the PDF…");
  try {
    const r = await api(`profiles/${PID}/summary/pdf`, {method: "POST", body: JSON.stringify(chosen())});
    if (r.status === 401) { location.replace("login.html"); return; }
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error((typeof j.detail === "string" && j.detail) || "Please check the choices."); }
    const blob = await r.blob();
    const name = (/filename="([^"]+)"/.exec(r.headers.get("content-disposition") || "") || [])[1] || "visit-summary.pdf";
    const url = URL.createObjectURL(blob), a = el("a", {href: url, download: name, hidden: true});
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    qSaved = $("#questions").value; qMark();   // the server kept these questions as the draft
    sayIn($("#buildMsg"), `Downloaded ${name}.`);
  } catch (e) { sayIn($("#buildMsg"), e.message, true); }
  finally { b.removeAttribute("aria-disabled"); }
};

/* ---------------------------------------------------------------- the measures ticked at first (#186): the profile's
   setting summary.ticked, here beside the summary since #402 (Settings → Dashboard before; Settings keeps it out of
   sight, for Start from a condition to fill in). Its owners and editors tick them, each tick saved as it's made with
   an Undo (#272), and the summary being made follows; someone who can only view the profile isn't shown it. */
const tickedSetting = pid => call(`profiles/${pid}/settings/sections`).then(d => d.sections.flatMap(s => s.settings).find(x => x.key === "summary.ticked"));
async function ticked() {
  const pid = PID, part = $("#sumTicked");
  part.hidden = true; sayIn($("#sumTickedMsg"), "");
  let st;
  try { st = await tickedSetting(pid); } catch (e) { return; }
  if (PID !== pid || !st || !st.can_change) return;
  // ids: focus goes back to the box an Undo drew again, from its toast (util.js, #419)
  $("#sumTickedBoxes").replaceChildren(...Object.entries(st.choices || {}).map(([id, label]) => el("label", {class: "check"},
    el("input", {type: "checkbox", id: "sumTick-" + id, value: id, checked: st.value.includes(id) || null, onchange: ev => tickSave(st, ev.target)}), " " + label)));
  part.hidden = false;
}
async function tickSave(st, input) {
  const pid = PID, msg = $("#sumTickedMsg"), name = $("#profile").selectedOptions[0]?.textContent.replace(/ \([^)]*\)$/, "");
  const value = [...document.querySelectorAll("#sumTickedBoxes input:checked")].map(i => i.value), was = {value: st.value, from: st.from};
  const path = `profiles/${pid}/settings/summary.ticked`, said = `Saved “${st.label}”.`, same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  try { await call(path, "PUT", {value}); }
  catch (e) { input.checked = !input.checked; sayIn(msg, e.message, true); return; }
  Object.assign(st, {value, from: "profile"});
  const box = document.querySelector(`#sections input[value="${input.value}"]`);
  if (box) { box.checked = input.checked; pvKeep = input.id; previewSoon(); }
  sayIn(msg, said, false, {say: false});
  toast(said, {action: "Undo", onAction: async () => {   // what it was, if it's still as ticked here
    const now = await tickedSetting(pid), back = !!now && same(now.value, value);
    if (back) { if (was.from === "default") await call(path, "DELETE"); else await call(path, "PUT", {value: was.value}); }
    const text = back ? `Undone: “${st.label}” is as before.` : `“${st.label}” was changed since, so it's left as it is.`;
    if (PID !== pid) { toast(`${name}: ${text}`); return; }   // another profile shown now (#367)
    if (back && box) { box.checked = !input.checked; pvKeep = input.id; previewSoon(); }
    await ticked();
    sayIn($("#sumTickedMsg"), text);
  }});
  inSight(input.id);
}

/* ---------------------------------------------------------------- follow the profile picker */
/* sharing.js fills it once it knows who's signed in (and the dates' format, dates.js), then sets its value; with a
   questions draft not saved, another profile is picked only once you say so (sidebar.js) */
(() => {
  const sel = $("#profile");
  const pick = () => {
    const v = +sel.value;
    if (!v || v === PID) return;
    PID = v;
    $("#summaryIntro").hidden = false;
    load();
    ticked();
  };
  sel.addEventListener("change", pick);
  new MutationObserver(pick).observe(sel, {childList: true});
})();
