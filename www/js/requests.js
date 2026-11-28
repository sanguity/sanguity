/* Sanguity · records requests (#1133; server/rec_requests.py): each request for the person's own records, who it went
   to, when and how, counted down to the response date of the rule they picked (US HIPAA 30 days and 30 more, UK/EU
   GDPR one month and two more, or their own), its letter drafted here, and once the records arrive, the import they
   came in. DATA.requests lists them, each with its dates worked out by the server (due, due_first, due_ext, rule_words)
   and its import ({id, at, records}) when linked. Facts only: "Sent Mar 3 · response due by Apr 2 (US HIPAA, 30 days)",
   "Extended to May 2", "Past the date you set"; never a verdict, never legal advice. Open ones are a line each on the
   Overview's Follow-ups card (rqCardRows); Add → Records request (#records-request) starts one; an import's result
   on the Sources page links it (#link-import-<id>). Plain script, loaded by index.html after followups.js. */
"use strict";
const RQ_HOW = [["", "Not said"], ["portal", "Patient portal"], ["email", "Email"], ["fax", "Fax"], ["post", "Post"], ["in person", "In person"]];
const RQ_RULES = [["hipaa", "US HIPAA: 30 days, plus one 30-day extension"], ["gdpr", "UK/EU GDPR: one month, extendable by two months"],
  ["other", "My own: a number of days I set"]];
const RQ_STATUS = [["waiting", "Waiting"], ["extended", "Extended"], ["received", "Received"], ["closed", "Closed"]];
const RQ_FORMATS = [["any", "An electronic copy, in any format you have"], ["fhir", "An electronic copy in FHIR format"],
  ["ccda", "An electronic copy in C-CDA format"], ["pdf", "An electronic copy as PDF"], ["paper", "A paper copy"]];
const rqAll = () => DATA && Array.isArray(DATA.requests) ? DATA.requests : [];
const rqById = id => rqAll().find(r => r.id === id);
const rqIsOpen = r => r.status === "waiting" || r.status === "extended";
const rqWord = (list, v) => (list.find(x => x[0] === v) || [v, v])[1];
const rqD = d => fmtD(dayTs(d));
/* "12 days left", "due today", "Past the date you set": counted in the person's own time zone (zToday) */
function rqLeft(r) {
  const n = Math.round((dayTs(r.due) - dayTs(zToday())) / DAY);
  return n < 0 ? "Past the date you set" : n === 0 ? "due today" : `${n} day${n === 1 ? "" : "s"} left`;
}
/* "Sent Mar 3 · response due by Apr 2 (US HIPAA, 30 days)" */
const rqSays = r => `Sent ${rqD(r.sent)} · response due by ${rqD(r.due_first)} (${r.rule_words})`;
/* the Overview's lines (followups.js fuCard): one per open request */
function rqCardRows() {
  return rqAll().filter(rqIsOpen).sort((a, b) => a.due.localeCompare(b.due)).map(r =>
    el("div", {class: "ov-row", "data-rq": r.id, ...pressable(ev => openFrom(ev.currentTarget, () => openRequest(r.id)))},
      el("span", {class: "n", text: `Records request to ${r.to}`}),
      el("span", {class: "r"}, ...keepDates(`due by ${rqD(r.due)} · ${rqLeft(r)}`)),
      el("span", {class: "s", text: `Sent ${rqD(r.sent)}${r.extended && r.due_ext ? ` · extended to ${rqD(r.due_ext)}` : ""}`})));
}
const rqSource = r => r.import && DATA.imports?.[r.import.id];
const rqSourceName = r => { const s = rqSource(r); return s ? s.name || s.label : "an import"; };

/* a request's own drawer */
function openRequest(id) {
  const r = rqById(id);
  if (!r) return closeDrawer();
  state.selEv = null;
  const body = drawerHead("letter", `Records request to ${r.to}`, rqSays(r));
  drawerStill = () => !!rqById(id);
  const wanted = r.from || r.until ? `From ${r.from ? rqD(r.from) : "the start"} to ${r.until ? rqD(r.until) : "now"}` : "All of them";
  const btn = (text, onclick) => canEdit() ? el("button", {class: "btn needs-server", type: "button", text, onclick}) : null;
  body.append(
    drawerFacts([["Response due", rqIsOpen(r) ? `${rqD(r.due)} · ${rqLeft(r)}` : rqD(r.due)],
      ["Status", rqWord(RQ_STATUS, r.status)], ["Received", r.received ? (r.import ? `${rqD(r.received)}, imported ${r.import.records} record${r.import.records === 1 ? "" : "s"}` : rqD(r.received)) : ""]], "facts"),
    drawerFacts([["Rule you picked", r.rule_words], ["Extension", r.extended && r.due_ext ? `Extended to ${rqD(r.due_ext)}` : r.due_ext ? `Not used (to ${rqD(r.due_ext)})` : "None"],
      ["Sent by", rqWord(RQ_HOW, r.how) === "Not said" ? "" : rqWord(RQ_HOW, r.how)], ["Records wanted", wanted + (r.kinds ? ` · ${r.kinds}` : "")],
      ["Format", rqWord(RQ_FORMATS, r.format)], ["Imported from", r.import ? rqSourceName(r) : ""],
      ["In the calendar feed", rqIsOpen(r) ? (r.show ? `As “Records request to ${r.to}: response due”` : "As “Sanguity records request”") : "Not listed"],
      ["Note", r.note], ["Added by", r.by || ""]]),
    el("div", {class: "btns"}, el("button", {class: "btn", type: "button", text: "The letter", onclick: () => rqLetterView(r)}),
      btn("Edit", () => rqForm(r)), btn(r.import ? "Change the import" : "Link an import", () => rqLinkView(r)), btn("Remove", () => rqRemove(r))),
    el("p", {class: "hint", text: "The dates follow the rule you picked, counted from the day it was sent. Sanguity keeps your record of the request; it isn't legal advice."}));
  showDrawer();
}
async function rqRemove(r) {
  const j = await ownSend("DELETE", `requests/${r.id}`);
  if (!j) return;
  closeDrawer();
  const t = toast(`Removed the records request to ${r.to}.`, {action: "Undo", onAction: async () => {
    const back = await ownSend("POST", "requests", j.removed);
    if (back) { openRequest(back.id); announce("The records request is back."); }
  }});
  requestAnimationFrame(() => { if (t.node?.isConnected) t.node.querySelector(".sg-toast-acts button")?.focus(); });
}

/* the letter: plain text, built here. The law is named only for a named rule; otherwise a plain request */
function rqLetter(r) {
  const p = personOf(), name = r.name || PROFILE.name, born = p.birthDate ? rqD(p.birthDate) : "";
  const wanted = r.from || r.until ? `My health records from ${r.from ? rqD(r.from) : "the start"} to ${r.until ? rqD(r.until) : "today"}` : "All of my health records";
  const law = r.rule === "hipaa" ? ["I am making this request under my right of access in the HIPAA Privacy Rule (45 CFR 164.524), which asks for a response within 30 days of receiving it."]
    : r.rule === "gdpr" ? ["This is a subject access request under Article 15 of the GDPR (or the UK GDPR). Article 12(3) asks for a response within one month of receiving it."] : [];
  return [rqD(r.sent), "", `To: ${r.to}`, "", "Request for a copy of my health records", "",
    "I am writing to ask for a copy of my health records.", ...law, "",
    `Name: ${name}`, `Date of birth: ${born || "________"}`,
    `Records wanted: ${wanted}${r.kinds ? ` (${r.kinds})` : ""}`, `Format: ${rqWord(RQ_FORMATS, r.format)}`, "",
    "Please let me know if you need anything more from me to send them.", "", "Thank you,", name, ""].join("\n");
}
function rqLetterView(r) {
  const body = drawerHead("letter", "The letter", `To ${r.to}`), text = rqLetter(r), msg = el("p", {class: "hint edit-msg", role: "status"});
  drawerStill = () => !!rqById(r.id);
  const file = `records-request-${r.sent}.txt`;
  const print = () => {   // the letter alone, as text, in a window of its own, printed from there
    const w = open(URL.createObjectURL(new Blob([text], {type: "text/plain;charset=utf-8"})));
    if (!w) return sayIn(msg, "The browser blocked the window: use Download, then print the file.", true);
    w.addEventListener("load", () => w.print(), {once: true});
  };
  body.append(el("p", {class: "hint", text: "Built from this request and About this person. Copy it into an email or your portal's message, or download or print it."}),
    el("div", {class: "jfield"}, el("textarea", {readonly: true, rows: Math.min(24, text.split("\n").length + 1), "aria-label": "The letter", text})),
    el("div", {class: "btns"}, el("button", {class: "btn primary", type: "button", text: "Copy", onclick: async () => {
      try { await navigator.clipboard.writeText(text); sayIn(msg, "Copied."); } catch (e) { sayIn(msg, "Couldn't copy here: select the text and copy it.", true); }
    }}), el("a", {class: "btn", download: file, href: "data:text/plain;charset=utf-8," + encodeURIComponent(text), text: "Download"}),
      el("button", {class: "btn", type: "button", text: "Print", onclick: print}),
      el("button", {class: "btn", type: "button", text: "Back", onclick: () => openRequest(r.id)})), msg,
    personOf().birthDate ? null : el("p", {class: "hint", text: "No date of birth is set: add it in Settings → This person → About this person, or write it in."}));
  showDrawer();
}

/* which import brought the records: the profile's imports, the newest first, each with Link */
function rqLinkView(r, only = null) {
  const body = drawerHead("letter", r.import ? "Change the import" : "Link an import", `Records request to ${r.to}`);
  drawerStill = () => !!rqById(r.id);
  const msg = el("p", {class: "hint edit-msg", role: "status"});
  const imps = Object.entries(DATA.imports || {}).map(([id, s]) => ({id: +id, ...s})).filter(s => only == null || s.id === only).sort((a, b) => b.at - a.at);
  const link = async id => { if (await ownSend("PUT", `requests/${r.id}/import`, {import_id: id}, msg)) { openRequest(r.id); announce(id == null ? "Unlinked." : "Linked."); } };
  body.append(el("p", {class: "hint", text: "The import the records came in. Linked, a request still waiting is marked received, on the day of the import."}),
    imps.length ? el("div", {}, ...imps.map(s => el("div", {class: "ov-row still"}, el("span", {class: "n", text: s.name || s.label}),
      el("span", {class: "r", text: fmtDT(s.at * 1000)}),
      el("button", {class: "btn small needs-server", type: "button", text: r.import?.id === s.id ? "Linked" : "Link", disabled: r.import?.id === s.id || null,
        "aria-label": `Link ${s.name || s.label}, ${fmtDT(s.at * 1000)}`, onclick: () => link(s.id)}))))
      : el("p", {text: "Nothing has been imported into this profile yet."}),
    el("div", {class: "btns"}, r.import ? el("button", {class: "btn needs-server", type: "button", text: "Unlink", onclick: () => link(null)}) : null,
      el("button", {class: "btn", type: "button", text: "Back", onclick: () => openRequest(r.id)})), msg);
  showDrawer();
}
/* #link-import-<id>, from an import's result (import-jobs.js): the open requests, each with Link this import */
function rqLinkResume() {
  const m = /^#link-import-(\d+)$/.exec(location.hash);
  if (!m) return;
  history.replaceState(history.state, "", location.pathname + location.search + (DATA ? tabHash() : ""));
  const id = +m[1], open = rqAll().filter(rqIsOpen);
  if (!canEdit() || !DATA) return;
  if (open.length === 1) return rqLinkView(open[0], id);
  const body = drawerHead("letter", "Link the import to a request", "Which request brought these records?");
  body.append(...open.map(r => el("div", {class: "ov-row", ...pressable(() => rqLinkView(r, id))},
    el("span", {class: "n", text: `Records request to ${r.to}`}), el("span", {class: "r", text: `Sent ${rqD(r.sent)}`}))),
    open.length ? null : el("p", {text: "No records request is waiting."}));
  showDrawer();
}

/* the add / change form, in the drawer. r: the request (null: a new one) */
function rqForm(r) {
  const body = drawerHead("letter", r ? "Change the records request" : "Records request", r ? `To ${r.to}` : "Your own log of a request for your records");
  if (r) drawerStill = () => !!rqById(r.id);
  const uid = Math.random().toString(36).slice(2, 8), id = k => `rq-${k}-${uid}`, msg = el("p", {class: "hint edit-msg", role: "status"}), inp = {};
  const row = (k, label, input) => { input.id = id(k); inp[k] = input; return el("div", {class: "jfield"}, el("label", {for: input.id, text: label}), input); };
  const pick = (list, v) => { const s = el("select", {}, ...list.map(([k, t]) => el("option", {value: k, text: t}))); s.value = v; return s; };
  const text = (v, max) => el("input", {type: "text", maxlength: max, autocomplete: "off", value: v || ""});
  const date = v => el("input", {type: "date", value: v || ""});
  const num = v => el("input", {type: "number", min: 0, max: 365, step: 1, inputmode: "numeric", value: v ?? ""});
  const f = r || {rule: "hipaa", status: "waiting", how: "", format: "any", sent: zToday(), name: PROFILE.name};
  const show = el("input", {type: "checkbox", id: id("show"), checked: f.show || null}), ext = el("input", {type: "checkbox", id: id("ext"), checked: f.extended || null});
  const own = el("div", {class: "jwhen"}, row("days", "Days to respond", num(f.days)), row("ext_days", "Extension, in days (optional)", num(f.ext_days || "")));
  const form = el("form", {class: "jform", novalidate: true},
    row("to", "Sent to", Object.assign(text(f.to, 200), {required: true, placeholder: "Riverside Clinic, medical records"})),
    el("div", {class: "jwhen"}, row("sent", "Sent on", date(f.sent)), row("how", "How", pick(RQ_HOW, f.how))),
    row("rule", "The rule you picked", pick(RQ_RULES, f.rule)), own,
    el("div", {class: "jwhen"}, row("status", "Status", pick(RQ_STATUS, f.status)), row("received", "Received on (optional)", date(f.received))),
    el("div", {class: "btns"}, el("label", {class: "toggle"}, ext, "Extension used")),
    el("div", {class: "jwhen"}, row("from", "Records from (optional)", date(f.from)), row("until", "Records until (optional)", date(f.until))),
    row("kinds", "Kinds of records (optional)", text(f.kinds, 300)), row("format", "Format wanted", pick(RQ_FORMATS, f.format)),
    row("name", "Your name, for the letter", text(f.name, 200)), row("note", "Note (optional)", el("textarea", {rows: 3, maxlength: 2000, text: f.note || ""})),
    el("div", {class: "btns"}, el("label", {class: "toggle"}, show, "Show who it went to in my calendar")),
    el("p", {class: "hint", text: "Calendar apps keep the feed on their servers, so unless you tick this, the response date says only “Sanguity records request” there."}),
    el("div", {class: "btns"}, el("button", {class: "btn primary needs-server", type: "submit", text: r ? "Save" : "Add request"}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => r ? openRequest(r.id) : closeDrawer()})), msg);
  const shown = () => { own.hidden = inp.rule.value !== "other"; };
  inp.rule.onchange = shown; shown();
  form.onsubmit = async ev => {
    ev.preventDefault();
    const bad = (t, x) => { sayIn(msg, t, true); x.focus(); };
    const v = k => inp[k].value.trim();
    if (!v("to")) return bad("Say who the request went to.", inp.to);
    if (v("rule") === "other" && !(+v("days") >= 1)) return bad("Give the number of days of your own rule.", inp.days);
    const out = {to: v("to"), sent: v("sent") || null, how: v("how"), rule: v("rule"), status: v("status"), received: v("received") || null,
      extended: ext.checked, from: v("from") || null, until: v("until") || null, kinds: v("kinds"), format: v("format"), name: v("name"),
      note: inp.note.value.trim(), show: show.checked, ...(v("rule") === "other" ? {days: +v("days"), ext_days: +v("ext_days") || 0} : {})};
    const j = await ownSend(r ? "PUT" : "POST", r ? `requests/${r.id}` : "requests", out, msg);
    if (!j) return;
    openRequest(j.id);
    announce(r ? "Saved." : "Records request added.");
  };
  body.append(form);
  showDrawer();
  inp.to.focus({preventScroll: true});
}
/* Add → Records request (nav.js #records-request), on this page or arriving from another */
function rqAddResume() {
  rqLinkResume();
  if (location.hash !== "#records-request") return;
  history.replaceState(history.state, "", location.pathname + location.search + (DATA ? tabHash() : ""));
  if (canEdit()) rqForm(null);
}
