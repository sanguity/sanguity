/* Sanguity · Settings → Install → General → Backups and logging (#1002, #998). Backups: the folder and the encryption
   key (its path and how many keys, never a key), each with what mounts it on this kind of install, and the schedule
   in a line linking to Install → Backups, where it's set (admin-backups.js draws that line again as it changes it:
   genBkSchedule). Logging: how long activity is kept (the install setting activity.days, ACTIVITY_DAYS), kept as
   typed and marked "not saved" until the group's bar saves it (genBkSave, settings-nav.js ADM) or puts it back
   (genBkDiscard); keeping fewer days, where that deletes entries, says how many and goes ahead only once that count
   is typed (admin.js confirmed, the server's one rule), with the fresh check; set by the environment, it's text (util.js envRow, #1119), naming
   its variable. Then where the app's output and its security log are read (read-only). call(), say(), plural() and
   fresh() are settings.js's and admin.js's. */
"use strict";
let GB = null, gbTyped = null;   // what the card was drawn from: {where, st (the setting), backups}; the days typed, not saved
const GB_KEY = "activity.days";

async function loadGenBackups() {
  try {
    const [where, inst, backups] = await Promise.all([call("settings/install/backups-logging"), call("settings/install"), call("backups")]);
    GB = {where, st: gbSetting(inst), backups, at: inst.settings_at};
  } catch (e) { $("#genBk").replaceChildren(el("p", {class: "msg bad", text: e.message})); return; }
  drawGenBackups();
}
const gbSetting = inst => inst.sections.flatMap(s => s.settings).find(st => st.key === GB_KEY);

/* "kv" lines: [name, value nodes, what sets it] */
const gbLines = rows => el("dl", {class: "kv"}, ...rows.flatMap(([dt, dd, by]) =>
  [el("dt", {}, dt), el("dd", {}, ...[].concat(dd), by ? el("div", {class: "hint"}, "Set by: " + by + ".") : null)]));
const gbSchedule = b => b.schedule === "off" ? "Off" : `${b.schedule[0].toUpperCase() + b.schedule.slice(1)}, keeping the newest ${plural(b.keep, "backup")}`;

function drawGenBackups() {
  const {where: w, st, backups: b} = GB, card = $("#genBk"), had = card.contains(document.activeElement) ? document.activeElement.id : null;
  let row, days = null;
  if (!st.can_change) row = envRow(st, GB.at, {value: plural(st.value, "day"), head: ["h3", {class: "sub"}]});   // (#1119) set by the environment
  else {
    days = el("input", {type: "number", id: "genActDays", class: "set-num", min: 30, max: 3650, step: 1, value: gbTyped ?? st.value, "aria-describedby": "genActHint"});
    row = setRow([el("h3", {class: "sub"}, el("label", {for: days.id, text: st.label}), el("span", {class: "tag set-unsaved", text: "not saved"})),
      el("p", {class: "hint", id: "genActHint", text: st.help})], days);
    days.oninput = () => { sayIn($("#genBkMsg"), ""); mark(); };
    days.onkeydown = ev => { if (ev.key === "Enter") { ev.preventDefault(); $("#setBar .btn.primary")?.click(); } };   // the bar's Save
  }
  const mark = () => {
    gbTyped = !days || days.value === String(st.value) ? null : days.value;
    row.classList.toggle("set-changed", gbTyped != null);
    if (gbTyped != null) $("#gen-backups").setAttribute("data-dirty", 1); else $("#gen-backups").removeAttribute("data-dirty");
  };
  const keys = w.key.keys == null ? "it can't be read here" : plural(w.key.keys, "key");
  const logs = w.logs.command ? [el("code", {text: w.logs.command}), " ", copyBtn(w.logs.command, {what: "the command"}), ` ${w.logs.where}.`] : w.logs.where + ".";
  card.replaceChildren(
    el("h3", {class: "sub", text: "Backups"}),
    gbLines([["Folder", el("code", {text: w.folder.path}), w.folder.set_by],
      ["Encryption key", [el("code", {text: w.key.file}), ` (${keys}; the first encrypts)`], w.key.set_by && "its folder, " + w.key.set_by],
      ["Schedule", [el("span", {id: "genBkSched", text: gbSchedule(b) + ". "}), el("a", {href: "#backups", text: "Change it under Backups"})]]]),
    el("h3", {class: "sub", text: "Logging"}),
    row,
    gbLines([["The app's output", logs],
      ["Security log", "In the same output, one line for each event, starting “security ”: sign-ins, refusals and the fresh check. Never health data."],
      ["Activity", [el("a", {href: "#installLog", text: "Install activity"}), " for the server's own; each profile's on its Sharing page."]]]),
    el("div", {id: "genBkReauth", class: "reauth", hidden: true}),
    el("span", {class: "msg", id: "genBkMsg", role: "status"}));
  mark();
  if (had && !card.contains(document.activeElement)) document.getElementById(had)?.focus({preventScroll: true});
}
/* Install → Backups changed its schedule: the line here too */
function genBkSchedule(b) {
  if (!GB) return;
  GB.backups = b;
  const line = $("#genBkSched");
  if (line) line.textContent = gbSchedule(b) + ". ";
}

/* the days to the server: where that deletes entries, it says how many, and it's done once that count is typed
   (admin.js confirmed). Gives back the install's settings, or null when the answer was no */
const gbPut = value => confirmed(typed => fresh(() => call(`settings/install/${GB_KEY}`, "PUT", {value, confirm: typed}), $("#genBkReauth")));
/* the bar's Save and Discard: the days kept */
async function genBkSave(msg, had) {
  const value = +gbTyped, what = `“${GB.st.label}” isn't saved: `;
  if (!Number.isInteger(value) || value < 30 || value > 3650) { sayIn(msg, `${what}keep from 30 to 3650 days.`, true); return; }
  const from = GB.st.value;
  let inst;
  try { inst = await gbPut(value); }
  catch (e) { sayIn(msg, what + (e.why || e.message), true); return; }
  if (!inst) return;   // (the answer was no: it stays in the bar)
  const said = `Saved: activity is kept for ${plural(value, "day")}.`;
  gbDrawn(inst, said);
  if (had) $("#genActDays").focus({preventScroll: true});
  // nothing goes before the next daily clean-up, so an Undo still keeps it all; only one still as this left it
  toast(said, {action: "Undo", onAction: async () => {
    const now = gbSetting(await call("settings/install"));
    if (now.value !== value) { gbDrawn({sections: [{settings: [now]}]}, "It was changed since, so it's left as it is."); return; }
    const back = await gbPut(from);
    if (back) gbDrawn(back, `Undone: activity is kept for ${plural(from, "day")}.`);
  }});
}
function gbDrawn(inst, said) {
  gbTyped = null; GB.st = gbSetting(inst);
  drawGenBackups();
  sayIn($("#genBkMsg"), said, false, {say: false});
}
function genBkDiscard() {
  gbTyped = null; drawGenBackups();
  sayIn($("#genBkMsg"), "Changes discarded: back as saved.");
  $("#genActDays")?.focus();
}
