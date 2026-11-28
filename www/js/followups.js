/* Sanguity · follow-ups from a visit (#1138; server/followups.py): the person's own list of things to do ("book the
   MRI", "repeat bloodwork in 3 months"), each with an optional date, the record it's about and done or not. DATA.followups
   lists them ({id, text, due, said, from, about: {id, title, t}, show, done, by...}). Its words say what was noted, never
   what's due: "You noted: repeat bloodwork by Jun 3", and a date that's gone by "date passed". Open ones are a card on
   the Overview (fuCard) and in the drawer of the record each is about (fuDrawerPart); owners and editors add one there
   or from Add → Follow-up (#add-followup), and in its own drawer mark it done, change or remove it. Every change's reply
   carries the records whole, put in place (ownSend). Plain script, loaded by index.html after overview.js; all files
   share one global scope. */
"use strict";
const FU_MAX = 300;
const fuAll = () => DATA && Array.isArray(DATA.followups) ? DATA.followups : [];
const fuDue = f => f.due ? (f.due < zToday() ? "date passed" : inDays(f.due)) : "";
const lowFirst = t => /^\p{Lu}\p{Ll}/u.test(t) ? t[0].toLowerCase() + t.slice(1) : t;
/* "You noted: repeat bloodwork by Jun 3" */
const fuSaid = f => `You noted: ${lowFirst(f.text)}${f.due ? " by " + fmtD(dayTs(f.due)) : ""}`;
/* the record it's about, if it's still here: by its id where that's the same day's, else by its title and day */
function fuEvent(f) {
  const a = f.about; if (!a) return null;
  const e = a.id != null && byId.get(a.id);
  if (e && (!a.t || isoDay(e.ts) === a.t)) return e;
  return EV.find(x => x.title === a.title && a.t && isoDay(x.ts) === a.t) || null;
}
/* a change sent to profiles/<id>/<path>; its reply's records put in place -> the reply, or null (said in msg, or
   announced). For follow-ups and records requests (requests.js) */
async function ownSend(method, path, body, msg) {
  const say = (t, bad) => msg ? sayIn(msg, t, bad) : announce(t);
  if (msg) sayIn(msg, "Saving…");
  let r, j;
  try { r = await api(`profiles/${PROFILE.id}/${path}`, {method, body: body ? JSON.stringify(body) : undefined}); j = await r.json().catch(() => ({})); }
  catch (err) { say("Couldn't reach the server: " + err.message, true); return null; }
  if (!r.ok) { say(typeof j.detail === "string" ? j.detail : `Couldn't save it (${r.status}).`, true); return null; }
  if (!j.records) location.reload();
  else if (!DATA) await recordsFirst(j.records);   // a profile with nothing imported: its first records
  else storeRecords(j.records);
  return j;
}
const fuById = id => fuAll().find(f => f.id === id);
const fuOrder = (a, b) => (a.due || "9999").localeCompare(b.due || "9999") || a.id - b.id;
/* a follow-up as a row: its words, its date and how far off ("in 3 weeks", "date passed"), what it's about */
function fuRow(f) {
  return el("div", {class: "ov-row", "data-fu": f.id, ...pressable(ev => openFrom(ev.currentTarget, () => openFollowup(f.id)))},
    el("span", {class: "n", text: f.text}), f.due ? el("span", {class: "r"}, ...keepDates(`by ${fmtD(dayTs(f.due))} · ${fuDue(f)}`)) : null,
    f.about ? el("span", {class: "s", text: `From ${f.about.title}${f.about.t ? " · " + fmtD(dayTs(f.about.t)) : ""}`}) : null);
}

/* the Overview's card (in Today): the open follow-ups, the soonest first, and the open records requests (requests.js)
   one line each; nothing when there are none */
function fuCard() {
  const open = fuAll().filter(f => !f.done).sort(fuOrder), reqs = typeof rqCardRows === "function" ? rqCardRows() : [];
  if (!open.length && !reqs.length) return null;
  const FEW = 4, card = el("div", {class: "card", id: "ovFollowups"}, el("h3", {text: "Follow-ups"}),
    el("p", {class: "hint", text: "What you noted to do"}));
  card.append(...open.slice(0, FEW).map(fuRow), ...reqs);
  if (open.length > FEW) card.append(el("p", {class: "hint mt-2", text: `${open.length - FEW} more noted`}));
  if (canEdit()) card.append(el("button", {class: "btn mt-3 needs-server", type: "button", text: "Add a follow-up", onclick: () => fuForm(null)}));
  return card;
}

/* in a record's drawer (drawer.js openEvent): the follow-ups noted about it, and Add a follow-up for owners and editors */
function fuDrawerPart(e) {
  const mine = fuAll().filter(f => fuEvent(f) === e).sort(fuOrder);
  const out = mine.length ? [el("div", {class: "sect", text: "Follow-ups"}), el("div", {}, ...mine.map(fuRow))] : [];
  if (canEdit()) out.push(el("div", {class: "btns"}, el("button", {class: "btn needs-server", type: "button", text: "Add a follow-up",
    "data-tip": "Something you were told to do, with a date if you like", onclick: () => fuForm(null, e)})));
  return out;
}

/* a follow-up's own drawer */
function openFollowup(id) {
  const f = fuById(id);
  if (!f) return closeDrawer();
  state.selEv = null;
  const body = drawerHead("visit", f.text, f.done ? "Follow-up · done" : "Follow-up");
  drawerStill = () => !!fuById(id);   // removed (or its import undone): closed (app.js, #517)
  const e = fuEvent(f);
  body.append(el("p", {text: fuSaid(f) + (f.said ? ` (${f.said} from ${fmtD(dayTs(f.from))})` : "") + "."}),
    ...present(drawerFacts([["Date", f.due ? `${fmtD(dayTs(f.due))} · ${fuDue(f)}` : "None given"],
      ["About", f.about ? `${f.about.title}${f.about.t ? " · " + fmtD(dayTs(f.about.t)) : ""}` : ""],
      ["Done", f.done ? (f.done_at ? fmtD(f.done_at * 1000) : "Yes") : "Not yet"],
      ["In the calendar feed", !f.due ? "Not listed: it has no date" : f.done ? "Not listed: it's done" : f.show ? `As “${f.text}”` : "As “Sanguity follow-up”"],
      ["Added by", f.by || ""]], "facts")),
    el("div", {class: "btns"}, e ? goBtn("Open the record", () => openEvent(e)) : null,
      canEdit() ? el("button", {class: "btn needs-server", type: "button", text: f.done ? "Mark not done" : "Mark done", onclick: async () => {
        const j = await ownSend("PUT", `followups/${f.id}`, {...f, done: !f.done});
        if (j) { openFollowup(f.id); announce(f.done ? "Marked not done." : "Marked done."); }
      }}) : null,
      canEdit() ? el("button", {class: "btn needs-server", type: "button", text: "Edit", onclick: () => fuForm(f)}) : null,
      canEdit() ? el("button", {class: "btn needs-server", type: "button", text: "Remove", onclick: () => fuRemove(f)}) : null),
    el("p", {class: "hint", text: "Your own note of what you were told, kept with the records. Sanguity doesn't decide what's due."}));
  showDrawer();
}
async function fuRemove(f) {
  const j = await ownSend("DELETE", `followups/${f.id}`);
  if (!j) return;
  closeDrawer();
  const t = toast(`Removed the follow-up “${f.text}”.`, {action: "Undo", onAction: async () => {
    const back = await ownSend("POST", "followups", j.removed);
    if (back) { openFollowup(back.id); announce("The follow-up is back."); }
  }});
  requestAnimationFrame(() => { if (t.node?.isConnected) t.node.querySelector(".sg-toast-acts button")?.focus(); });
}

/* the add / change form, in the drawer. f: the follow-up (null: a new one); e: the record it's about (a new one's) */
function fuForm(f, e = null) {
  const about = f ? f.about : e ? {id: e.id, title: e.title, t: isoDay(e.ts)} : null, from = about?.t || zToday();
  const body = drawerHead("visit", f ? "Change the follow-up" : "Add a follow-up", about ? `About ${about.title} · ${fmtD(dayTs(about.t || from))}` : "Your own note");
  if (f) drawerStill = () => !!fuById(f.id);
  const uid = Math.random().toString(36).slice(2, 8), id = k => `fu-${k}-${uid}`, msg = el("p", {class: "hint edit-msg", role: "status"});
  const row = (label, input) => el("div", {class: "jfield"}, el("label", {for: input.id, text: label}), input);
  const text = el("input", {type: "text", id: id("text"), maxlength: FU_MAX, autocomplete: "off", required: true, placeholder: "Book the MRI"});
  text.value = f ? f.text : "";
  const when = el("select", {id: id("when")}, el("option", {value: "", text: "No date"}), el("option", {value: "on", text: "On a date"}),
    el("option", {value: "in", text: about ? "A time after the visit" : "A time from today"}));
  const due = el("input", {type: "date", id: id("due")}), n = el("input", {type: "number", id: id("n"), min: 1, max: 600, step: 1, inputmode: "numeric"});
  const unit = el("select", {id: id("unit")}, ...[["weeks", "Weeks"], ["months", "Months"], ["days", "Days"]].map(([v, t]) => el("option", {value: v, text: t})));
  const m = f?.said && /^in (\d+) (day|week|month)/.exec(f.said);
  when.value = m ? "in" : f?.due ? "on" : "";
  due.value = f?.due || ""; n.value = m ? m[1] : "3"; unit.value = m ? m[2] + "s" : "months";
  const onBox = row("Date", due), inBox = el("div", {class: "jwhen"}, row("How many", n), row("Of", unit));
  const shown = () => { onBox.hidden = when.value !== "on"; inBox.hidden = when.value !== "in"; };
  when.onchange = shown; shown();
  const show = el("input", {type: "checkbox", id: id("show"), checked: f?.show || null});
  const form = el("form", {class: "jform", novalidate: true}, row("What to do", text), row("When", when), onBox, inBox,
    el("div", {class: "btns"}, el("label", {class: "toggle"}, show, "Show its words in my calendar")),
    el("p", {class: "hint", text: "Calendar apps keep the feed on their servers, so unless you tick this, a follow-up with a date says only “Sanguity follow-up” there."}),
    el("div", {class: "btns"}, el("button", {class: "btn primary needs-server", type: "submit", text: f ? "Save" : "Add follow-up"}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => f ? openFollowup(f.id) : e ? openEvent(e) : closeDrawer()})), msg);
  form.onsubmit = async ev => {
    ev.preventDefault();
    const bad = (t, x) => { sayIn(msg, t, true); x.focus(); };
    if (!text.value.trim()) return bad("Say what the follow-up is.", text);
    const out = {text: text.value.trim(), about, show: show.checked, done: f ? f.done : false};
    if (when.value === "on") { if (!due.value) return bad("Give the date.", due); out.due = due.value; }
    if (when.value === "in") {
      const k = +n.value;
      if (!Number.isInteger(k) || k < 1 || k > 600) return bad("Give a whole number from 1 to 600.", n);
      if (m && k === +m[1] && unit.value === m[2] + "s") Object.assign(out, {due: f.due, said: f.said, from: f.from});   // as it was
      else Object.assign(out, {in_n: k, in_unit: unit.value, from});
    }
    const j = await ownSend(f ? "PUT" : "POST", f ? `followups/${f.id}` : "followups", out, msg);
    if (!j) return;
    openFollowup(j.id);
    announce(f ? "Saved." : "Follow-up added.");
  };
  body.append(form);
  showDrawer();
  text.focus({preventScroll: true});
}
/* Add → Follow-up (nav.js #add-followup), on this page or arriving from another: the form, the address the tab's again */
function fuAddResume() {
  if (location.hash !== "#add-followup") return;
  history.replaceState(history.state, "", location.pathname + location.search + (DATA ? tabHash() : ""));
  if (canEdit()) fuForm(null);
}
