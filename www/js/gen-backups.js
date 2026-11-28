/* Sanguity · Settings → Install: two cards from what General → Backups and logging was (#1002, #998; the owner's
   decision on #1153). Backups → Folder and key (#bk-files): the folder and the encryption key (its path and how many
   keys, never a key), each with what mounts it on this kind of install, beside the schedule and the rest of Backups
   (admin-backups.js). Logs and activity → Logging (#logging): how long activity is kept (the install setting
   activity.days, ACTIVITY_DAYS), kept as typed and marked "not saved" until the group's bar saves it (genBkSave,
   settings-nav.js ADM) or puts it back (genBkDiscard); keeping fewer days, where that deletes entries, says how
   many and goes ahead only once that count is typed (admin.js confirmed, the server's one rule), with the fresh
   check (#1158: the environment sets it no longer). Then where the app's output and its security log are read (read-only). call(), say(), plural() and
   fresh() are settings.js's and admin.js's. */
"use strict";
let GB = null, gbTyped = null;   // what the cards were drawn from: {where, st (the setting), at}; the days typed, not saved
const GB_KEY = "activity.days";

async function loadGenBackups() {
  try {
    const [where, inst] = await Promise.all([call("settings/install/backups-logging"), call("settings/install")]);
    GB = {where, st: gbSetting(inst), at: inst.settings_at};
  } catch (e) { for (const c of ["#bkFiles", "#genBk"]) $(c).replaceChildren(el("p", {class: "msg bad", text: e.message})); return; }
  const {key: k, folder: f} = GB.where, keys = k.keys == null ? "it can't be read here" : plural(k.keys, "key");
  $("#bkFiles").replaceChildren(gbLines([["Folder", el("code", {text: f.path}), f.set_by],
    ["Encryption key", [el("code", {text: k.file}), ` (${keys}; the first encrypts)`], k.set_by && "its folder, " + k.set_by]]));
  drawGenBackups();
}
const gbSetting = inst => inst.sections.flatMap(s => s.settings).find(st => st.key === GB_KEY);

/* "kv" lines: [name, value nodes, what sets it] */
const gbLines = rows => el("dl", {class: "kv"}, ...rows.flatMap(([dt, dd, by]) =>
  [el("dt", {}, dt), el("dd", {}, ...[].concat(dd), by ? el("div", {class: "hint"}, "Set by: " + by + ".") : null)]));

/* Logging: the days kept, then where the logs are read */
function drawGenBackups() {
  const {where: w, st} = GB, card = $("#genBk"), had = card.contains(document.activeElement) ? document.activeElement.id : null;
  let row, days = null;
  {
    days = el("input", {type: "number", id: "genActDays", class: "set-num", min: 30, max: 3650, step: 1, value: gbTyped ?? st.value, "aria-describedby": "genActHint"});
    row = setRow([el("h3", {class: "sub"}, el("label", {for: days.id, text: st.label}), el("span", {class: "tag set-unsaved", text: "not saved"})),
      el("p", {class: "hint", id: "genActHint", text: st.help})], days);
    days.oninput = () => { sayIn($("#genBkMsg"), ""); mark(); };
    days.onkeydown = ev => { if (ev.key === "Enter") { ev.preventDefault(); $("#setBar .btn.primary")?.click(); } };   // the bar's Save
  }
  const mark = () => {
    gbTyped = !days || days.value === String(st.value) ? null : days.value;
    row.classList.toggle("set-changed", gbTyped != null);
    if (gbTyped != null) $("#logging").setAttribute("data-dirty", 1); else $("#logging").removeAttribute("data-dirty");
  };
  const logs = w.logs.command ? [el("code", {text: w.logs.command}), " ", copyBtn(w.logs.command, {what: "the command"}), ` ${w.logs.where}.`] : w.logs.where + ".";
  card.replaceChildren(
    row,
    gbLines([["The app's output", logs],
      ["Security log", "In the same output, one line for each event, starting “security ”: sign-ins, refusals and the fresh check. Never health data."],
      ["Activity", [el("a", {href: "#installLog", text: "Install activity"}), ", below, for the server's own; each profile's on its Sharing page."]]]),
    el("div", {id: "genBkReauth", class: "reauth", hidden: true}),
    el("span", {class: "msg", id: "genBkMsg", role: "status"}));
  mark();
  if (had && !card.contains(document.activeElement)) document.getElementById(had)?.focus({preventScroll: true});
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
