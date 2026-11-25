/* Sanguity · the Admin page's Backups (#200, server/backup_jobs.py): when the newest backup was made, how big it is
   and where it went, with a warning when it's late (on every group of the page, and beside Backups in the sidebar);
   Back up now and Check a backup, which run in the background while this polls how they go; and the schedule.
   What the backups take of the watch receiver's data, and a check's counts of it (#227).
   Restore: what a restore replaced, kept in the data folder until it's deleted, with Delete now (#595).
   Turning the schedule off, or keeping fewer backups than would stay, is asked first (ask(), util.js) and needs the
   fresh check (admin.js fresh()). Uses admin.js's call(), say(), when() and plural(). */
"use strict";
const BK_TABLES = [["users", "account"], ["profiles", "profile"], ["events", "record"], ["results", "lab result"],
  ["documents", "document"], ["imports", "import"]];   // the tables a check's result names, in words
const BK_WATCH = [["readings", "Reading"], ["sleep", "Sleep record"], ["workouts", "Workout"], ["ecgs", "ECG"],
  ["uploads", "Archived upload"]];   // the watch receiver's data in a check's result (#227)
const BK_STAGE = {archiving: "Writing the backup", checksums: "Checking every file against its checksum",
  restoring: "Restoring into a temporary folder", database: "Checking the restored database"};
const bkSize = n => n == null ? "unknown" : n >= 2 ** 30 ? (n / 2 ** 30).toFixed(1) + " GB" : n >= 2 ** 20 ? (n / 2 ** 20).toFixed(1) + " MB"
  : n >= 1024 ? Math.round(n / 1024) + " KB" : `${n} bytes`;
const bkAgo = s => s < 3600 ? "less than an hour ago" : s < 2 * 86400 ? plural(Math.floor(s / 3600), "hour") + " ago" : plural(Math.floor(s / 86400), "day") + " ago";
const bkRemoves = (n, sched, keep) => sched === "off" ? 0 : Math.max(0, n + 1 - keep);   // as backup_jobs.removes
let BK = null, bkPolling = null, bkShowAll = false;

async function loadBackups() {
  try { drawBackups(await call("backups")); }
  catch (e) { $("#bkStatus").replaceChildren(el("p", {class: "msg bad", text: e.message})); }
}

function drawBackups(st) {
  const was = BK && BK.running;
  BK = st;
  const now = Date.now() / 1000;
  $("#bkKeyFile").replaceChildren("On this server the key file is ", el("code", {text: st.key_file}), ".");
  // the newest backup: when, how big, where; then whether it's late
  const n = st.newest;
  const state = {ok: ["good", "Up to date.", "check"], none: ["", "No backups yet: back up now, or turn on a schedule below."],
    late: ["bad", st.warning, "triangle-alert"], failed: ["bad", st.warning, "triangle-alert"]}[st.state];
  $("#bkStatus").replaceChildren(
    el("p", {class: "bk-state " + state[0], id: "bkState"}, state[2] ? [icon(state[2]), " "] : null, state[1]),
    el("dl", {class: "kv"},
      el("dt", {text: "Newest"}), el("dd", {text: n ? `${when(n.at)} (${bkAgo(now - n.at)})` : "none yet"}),
      el("dt", {text: "Size"}), el("dd", {text: n ? `${bkSize(n.size)}; ${plural(st.backups.length, "backup")} in the folder, ${bkSize(st.total_size)} in all` : "—"}),
      el("dt", {text: "Where"}), el("dd", {}, el("code", {text: st.folder}),
        st.inside_data ? " (inside the data folder, on the same disk as the data: copy the backups somewhere else too)" : " (BACKUP_DIR in .env)"),
      st.free != null ? el("dt", {text: "Free space"}) : null, st.free != null ? el("dd", {text: bkSize(st.free)}) : null,
      el("dt", {text: "Schedule"}), el("dd", {text: st.schedule === "off" ? "off" : `${st.schedule}, keeping the newest ${st.keep}` +
        (st.next ? `; next ${st.next <= now ? "now" : when(st.next)}` : "")}),
      el("dt", {text: "Phone and watch"}), el("dd", {id: "bkWatch", text: bkWatchSays(st.watch)})));
  // the warning on every group, and beside Backups in the sidebar
  const notice = $("#backupNotice");
  notice.hidden = !st.late;
  notice.replaceChildren(...(st.late ? [el("b", {text: "Backups need attention. "}), st.warning + " ",
    el("a", {href: "#backups", text: "Go to Backups"})] : []));
  const link = document.querySelector("#admNav a[data-group=backups]");
  if (link) {
    link.querySelector(".bk-late")?.remove();
    if (st.late) link.append(el("span", {class: "tag bk-late", text: "late"}));
  }
  drawSchedule(st);
  drawList(st);
  drawReplaced(st);
  drawRunning(st.running);
  if (st.running) poll();
  else if (was) finished(st.runs.find(r => r.id === was.id));
}

/* what the next backup takes of the watch receiver's data (#227, backup.watch_status) */
function bkWatchSays(w) {
  if (!w || !w.visible) return "Not in the backups: the app can't see the watch receiver's folder here. If a phone " +
    "uploads to this server, Backup and restore in the README says what to add to the install.";
  if (!w.found) return "None yet: the watch receiver hasn't kept anything from a phone.";
  const saved = w.saved ? `as the receiver last saved it, ${when(w.saved)}` : "though the receiver hasn't saved it yet";
  return `In the backups: the receiver's data for ${plural(w.profiles, "profile")}, ${saved}, and every upload it archived.` +
    (w.after ? ` ${plural(w.after, "upload")} came after that save: in the backup as ${w.after === 1 ? "it" : "they"} ` +
      `arrived, and folded in when the receiver starts after a restore.` : "");
}

/* ---------------------------------------------------------------- Back up now, and following a run */
$("#bkNow").onclick = async () => {
  try { const r = await call("backups", "POST"); say("#bkNowMsg", "Backing up…"); BK.running = {id: r.id, kind: "backup", state: "queued"}; drawRunning(BK.running); poll(); }
  catch (e) { say("#bkNowMsg", e.message, true); }
};
function drawRunning(run) {
  const box = $("#bkProgress");
  $("#bkNow").disabled = !!run;
  for (const b of document.querySelectorAll("#bkList button")) b.disabled = !!run;
  box.hidden = !run;
  if (!run) { box.replaceChildren(); return; }
  const pct = run.total ? Math.min(100, Math.round(100 * run.done / run.total)) : null;
  const what = run.kind === "backup" ? "Backing up" : `Checking the backup from ${bkWhen(run.file)}`;
  box.replaceChildren(el("span", {text: `${what}: ${BK_STAGE[run.stage] || "starting"}${pct != null ? `, ${pct}%` : ""}…`}),
    el("progress", {max: 100, ...(pct != null ? {value: pct} : {}), "aria-label": what}));
}
function poll() {
  if (bkPolling) return;
  bkPolling = setTimeout(async () => {
    bkPolling = null;
    try { drawBackups(await call("backups")); } catch (e) { say("#bkNowMsg", e.message, true); }
  }, 800);
}
function finished(run) {
  if (!run) return;
  if (run.kind === "backup") {
    if (run.state === "done") say("#bkNowMsg", `Backed up: ${run.file}, ${bkSize(run.size)}` +
      (run.result && run.result.removed && run.result.removed.length ? `. Removed ${plural(run.result.removed.length, "older backup")}.` : "."));
    else say("#bkNowMsg", `The backup failed: ${run.message}`, true);
  } else drawResult(run);
}

/* ---------------------------------------------------------------- the schedule */
/* Saved as Settings are (#272, #409): how often, a simple preference, as it's picked, a toast saying so with an Undo;
   how many to keep, a value, kept as typed, marked "not saved" and counted in the card's data-dirty until the page's
   bar (sidebar.js, admin.js) saves it (bkKeepSave) or puts it back (bkKeepDiscard). Turning the schedule off, or
   keeping fewer backups than would stay, is still asked first and needs the fresh check, an Undo's too. bkTyped: the
   number typed and not saved, kept while the card is drawn again (a run's progress draws it again). */
let bkTyped = null;
function drawSchedule(st) {
  const form = $("#bkSchedule"), had = form.contains(document.activeElement) ? document.activeElement.id : null;
  const opts = [["off", "Off"], ["daily", "Daily"], ["weekly", "Weekly"]];
  const keep = el("input", {type: "number", id: "bkKeep", min: 1, max: st.keep_max, step: 1, value: bkTyped ?? st.keep, class: "set-num", required: true});
  const line = el("label", {class: "bk-keep"}, "Keep the newest ", keep, " backups", el("span", {class: "tag set-unsaved", text: "not saved"}));
  const mark = () => {
    bkTyped = keep.value === String(st.keep) ? null : keep.value;
    line.classList.toggle("set-changed", bkTyped != null);
    if (bkTyped != null) $("#backups").setAttribute("data-dirty", 1); else $("#backups").removeAttribute("data-dirty");
  };
  keep.oninput = () => { sayIn($("#bkSchedMsg"), ""); mark(); };
  form.replaceChildren(   // setting rows (#286, util.js): what each is on the left, its control on the right
    setRow(el("p", {class: "set-row-name", id: "bkSchedLbl", text: "How often"}),
      el("fieldset", {class: "set-radios", "aria-labelledby": "bkSchedLbl"},
        el("div", {class: "seg wrap", id: "bkSchedSeg"}, ...opts.map(([v, l]) => el("label", {},
          el("input", {type: "radio", name: "bkSched", id: "bkSched-" + v, value: v, checked: v === st.schedule, onchange: () => bkFreq(v)}), l))))),
    setRow([el("p", {class: "set-row-name"}, "How many to keep",   // its ⓘ beside its name, a line of hint under it (#494)
        aboutWords({title: "keeping backups"}, "While a schedule is on, older backups in the folder are deleted after each new one, keeping this many. " +
          "Only Sanguity's own backup files are counted or deleted; anything else in the folder is left alone.",
          "The app makes the backup when the newest is a day (or a week) old, whoever made it.")),
      el("p", {class: "hint", text: "The oldest go after each scheduled one."})],
      line),
    el("span", {class: "msg", id: "bkSchedMsg", role: "status"}));
  form.onsubmit = ev => { ev.preventDefault(); $("#admBar .btn.primary")?.click(); };   // Enter: the bar's Save
  mark();
  if (had && !form.contains(document.activeElement)) document.getElementById(had)?.focus({preventScroll: true});
}
/* a schedule and count to the server, asked first where it turns backups off or deletes more of them: gives back the
   new state, or null when the answer was no */
async function bkPut(body) {
  const st = BK, n = st.backups.length, more = bkRemoves(n, body.schedule, body.keep) - bkRemoves(n, st.schedule, st.keep);
  if (body.schedule === "off" && st.schedule !== "off" && !await ask({title: "Turn off scheduled backups?",
      message: ["The app stops making backups by itself. The backups in the folder stay.",
                "Only do this if something else makes them, like a job on the server."], action: "Turn off", danger: true})) return null;
  if (more > 0 && !await ask({title: `Keep only the newest ${plural(body.keep, "backup")}?`,
      message: `The next backup then deletes ${more === 1 ? "the oldest backup" : `the ${more} oldest backups`} from the folder.`,
      action: "Keep fewer", danger: true})) return null;
  const st2 = await fresh(() => call("backups/schedule", "PUT", body), $("#bkReauth"));
  drawBackups(st2);
  return st2;
}
const bkSays = st => st.schedule === "off" ? "Scheduled backups are off." : `Backups are scheduled ${st.schedule}, keeping the newest ${st.keep}.`;
/* focus fallen to the page (the check's box gone with it, #536, #541): on what's given */
const bkBack = to => [null, document.body].includes(document.activeElement) && to()?.focus();
const bkKeepIn = () => $("#bkKeep"), bkSchedIn = () => $("input[name=bkSched]:checked");
/* an Undo: the schedule or the count put back, if it's still as this change left it. After the check's Confirm focus
   is on what it put back; after its Cancel on the Undo again, the toast saying why once (#541) */
const bkUndo = (part, from, to) => async () => {
  const undo = document.activeElement;
  drawBackups(await call("backups"));
  if (BK[part] !== to) { sayIn($("#bkSchedMsg"), "The schedule was changed since, so it's left as it is."); return; }
  let st;
  try { st = await bkPut({schedule: BK.schedule, keep: BK.keep, [part]: from}); }
  catch (e) { bkBack(() => undo.isConnected ? undo : null); throw new Error(e.why || e.message); }
  if (!st) return false;   // (asked, and the answer was no: the toast stays)
  bkBack(part === "keep" ? bkKeepIn : bkSchedIn);
  sayIn($("#bkSchedMsg"), "Undone: " + bkSays(st));
};
/* how often: saved as it's picked; focus back on it after the check's Confirm or Cancel (#536: it went with the box) */
async function bkFreq(v) {
  const from = BK.schedule, back = () => bkBack(bkSchedIn);
  let st;
  try { st = await bkPut({schedule: v, keep: BK.keep}); }
  catch (e) { drawSchedule(BK); sayIn($("#bkSchedMsg"), `Not saved: ${e.why || e.message}`, true); back(); return; }
  if (!st) { drawSchedule(BK); return; }   // (the answer was no: as it was)
  back();
  const said = bkSays(st);
  sayIn($("#bkSchedMsg"), said, false, {say: false});
  toast(said, {action: "Undo", onAction: bkUndo("schedule", from, v)});
}
/* the bar's Save and Discard: the count kept */
async function bkKeepSave(msg, had) {
  const keep = +bkTyped, from = BK.keep, what = "“Keep the newest” isn't saved: ";
  if (!Number.isInteger(keep) || keep < 1 || keep > BK.keep_max) { sayIn(msg, `${what}keep between 1 and ${BK.keep_max} backups.`, true); return; }
  let st;
  try { st = await bkPut({schedule: BK.schedule, keep}); }
  catch (e) { sayIn(msg, what + (e.why || e.message), true); if (!had) bkBack(bkKeepIn); return; }   // (Enter, then the check's Cancel: #541; from the bar, on its Save)
  if (!st) return;   // (the answer was no: it stays in the bar)
  bkTyped = null; drawSchedule(st);
  const said = `Saved: keeping the newest ${plural(st.keep, "backup")}.`;
  sayIn($("#bkSchedMsg"), said, false, {say: false});
  if (had) $("#bkKeep").focus({preventScroll: true});
  else bkBack(bkKeepIn);
  toast(said, {action: "Undo", onAction: bkUndo("keep", from, keep)});
}
function bkKeepDiscard() {
  bkTyped = null; drawSchedule(BK);
  sayIn($("#bkSchedMsg"), "Changes discarded: back as saved.");
  $("#bkKeep").focus();
}

/* ---------------------------------------------------------------- the backups in the folder, and checking one */
const bkWhen = name => { const m = /(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)/.exec(name || "");
  return m ? when(new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000) : name; };
function drawList(st) {
  const box = $("#bkList");
  if (!st.backups.length) { box.replaceChildren(emptyState("No backups in the folder yet: each one made is listed here, to check.", null, {cls: ""})); return; }   // (#494)
  const shown = bkShowAll ? st.backups : st.backups.slice(0, 5);
  box.replaceChildren(el("div", {class: "tbl-wrap"}, el("table", {class: "tbl bk-list"},
    el("thead", {}, el("tr", {}, ...["Made", "Size", "Last checked", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...shown.map(b => el("tr", {"data-backup": b.name},
      el("td", {}, when(b.at), el("div", {class: "muted bk-name", text: b.name})),
      el("td", {text: bkSize(b.size)}),
      el("td", {}, b.checked ? [icon(b.checked.ok ? "check" : "x"), ` ${b.checked.ok ? "good" : "not good"}, ${when(b.checked.at)}`] : "not yet"),
      el("td", {class: "acts"}, el("button", {class: "btn small", type: "button", text: "Check", disabled: !!st.running,
        "aria-label": `Check the backup from ${when(b.at)}`, onclick: () => checkBackup(b)})))))),
    st.backups.length > 5 ? el("button", {class: "btn small", type: "button", text: bkShowAll ? "Show fewer" : `Show all ${st.backups.length}`,
      onclick: () => { bkShowAll = !bkShowAll; drawList(BK); }}) : null));
}
async function checkBackup(b) {
  try {
    const r = await call("backups/check", "POST", {name: b.name});
    $("#bkResult").replaceChildren();
    BK.running = {id: r.id, kind: "check", file: b.name, state: "queued"};
    drawRunning(BK.running);
    poll();
  } catch (e) { say("#bkNowMsg", e.message, true); }
}
function drawResult(run) {
  const box = $("#bkResult"), res = run.result || {};
  if (run.state !== "done") {
    box.replaceChildren(el("p", {class: "bk-state bad"}, icon("x"), ` The check didn't finish: ${run.message}`));
    return;
  }
  const counts = res.counts || {}, now = res.now || {}, w = res.watch;
  const rows = BK_TABLES.filter(([t]) => t in counts);
  const words = rows.map(([t, w]) => `${counts[t].backup.toLocaleString("en-US")} ${w}${counts[t].backup === 1 ? "" : "s"}`).join(", ");
  box.replaceChildren(...present(
    run.ok ? el("p", {class: "bk-state good"}, el("b", {}, icon("check"), " Good. "),
      `The backup from ${bkWhen(run.file)} restored into a temporary folder: its ${plural(res.files, "file")} match their checksums, ` +
      `this install's key opens it, and its database is intact with the rows it had when it was made (${words}). The copy was deleted.`)
    : el("p", {class: "bk-state bad"}, el("b", {}, icon("x"), " Not good. "), `The backup from ${bkWhen(run.file)}: ${run.message} ` +
      "Make a new backup, and don't rely on this one. The temporary copy was deleted."),
    rows.length ? el("div", {class: "tbl-wrap"}, el("table", {class: "tbl bk-counts"},
      el("caption", {text: "Rows in the backup, and in the live data now (it has changed since)"}),
      el("thead", {}, el("tr", {}, ...["", "Backup", "Expected", "Now"].map(h => el("th", {text: h})))),
      el("tbody", {}, ...rows.map(([t, w]) => el("tr", {},
        el("th", {scope: "row", text: w[0].toUpperCase() + w.slice(1) + "s"}),
        el("td", {text: counts[t].backup.toLocaleString("en-US")}),
        el("td", {text: counts[t].expected == null ? "not recorded" : counts[t].expected.toLocaleString("en-US")}),
        el("td", {text: t in now ? now[t].toLocaleString("en-US") : "—"})))))) : null,
    run.ok ? el("p", {class: "bk-watch", text: bkWatchChecked(w)}) : null,
    w && w.counts ? el("div", {class: "tbl-wrap"}, el("table", {class: "tbl bk-counts bk-watch-counts"},
      el("caption", {text: "Phone and watch data in the backup, and in the receiver's folder now"}),
      el("thead", {}, el("tr", {}, ...["", "Backup", "Expected", "Now"].map(h => el("th", {text: h})))),
      el("tbody", {}, ...BK_WATCH.filter(([k]) => k in w.counts).map(([k, label]) => el("tr", {},
        el("th", {scope: "row", text: label + "s"}),
        el("td", {text: w.counts[k].backup.toLocaleString("en-US")}),
        el("td", {text: w.counts[k].expected == null ? "not recorded" : w.counts[k].expected.toLocaleString("en-US")}),
        el("td", {text: w.now && k in w.now ? w.now[k].toLocaleString("en-US") : "—"})))))) : null));
}
/* the watch receiver's data in a good backup (#227): when the receiver had last saved it, and what came after */
function bkWatchChecked(w) {
  if (!w) return "It has no phone and watch data: there was none when it was made, or an older version made it.";
  const saved = w.saved ? `, as the receiver last saved it ${when(Date.parse(w.saved) / 1000)}` : "";
  return `Its phone and watch data, for ${plural(w.profiles, "profile")}, opens and is intact too${saved}` +
    (w.after ? `, with ${plural(w.after, "upload")} that came after that save (folded in when the receiver starts after a restore).` : ".");
}

/* ---------------------------------------------------------------- what a restore replaced (#595) */
/* Restoring stays a command-line step; what it replaces is moved aside in the data folder (before-restore-<time>/)
   and deleted replaced_days after (backup_jobs.sweep_replaced): it may hold profiles deleted since. So is the phone and
   watch data it left to be moved in by hand (watch-restored-<time>/, kind "watch", #603). Each one is listed with when
   it goes, and Delete now, asked first and with the fresh check. Focus stays on the button it was on while a run's
   progress draws the list again. */
const bkLeft = r => r.kind === "watch";   // (#603)
const bkWhat = r => bkLeft(r) ? `the phone and watch data the restore of ${when(r.made)} left to move in` : `what the restore of ${when(r.made)} replaced`;
function drawReplaced(st) {
  const box = $("#bkReplaced"), list = st.replaced || [], now = Date.now() / 1000;
  const had = box.contains(document.activeElement) ? document.activeElement.dataset.replaced : null;
  const made = r => `The restore of ${when(r.made)}` + (bkLeft(r) ? ", its phone and watch data to move in by hand" : "");
  box.replaceChildren(
    el("p", {class: "hint"}, "Restoring a backup is done on the server with the app stopped (",
      el("code", {text: "python -m server.manage restore"}), "), never from this page. What it replaces is kept in the data " +
      `folder, in before-restore-<time>/, and deleted ${plural(st.replaced_days, "day")} after the restore: it may hold ` +
      "profiles deleted since. Backups never include it. Where the watch receiver's folder can't be written, the " +
      "backup's phone and watch data is left in watch-restored-<time>/ to be moved in by hand, and kept as long."),
    list.length ? el("div", {class: "tbl-wrap"}, el("table", {class: "tbl bk-replaced"},
      el("caption", {text: "What restores replaced or left, kept in the data folder now"}),
      el("thead", {}, el("tr", {}, ...["Kept from", "Deleted", ""].map(h => el("th", {text: h})))),
      el("tbody", {}, ...list.map(r => el("tr", {"data-replaced": r.name},
        el("td", {}, made(r), el("div", {class: "muted bk-name", text: r.name})),
        el("td", {text: r.deletes <= now ? "within the hour" : when(r.deletes)}),
        el("td", {class: "acts"}, el("button", {class: "btn small", type: "button", text: "Delete now", "data-replaced": r.name,
          "aria-label": `Delete now ${bkWhat(r)}`, onclick: () => bkDeleteReplaced(r)})))))))
    : el("p", {class: "muted", id: "bkReplacedNone", text: "Nothing a restore replaced is kept now."}));
  if (had) box.querySelector(`button[data-replaced="${CSS.escape(had)}"]`)?.focus({preventScroll: true});
}
async function bkDeleteReplaced(r) {
  const what = bkWhat(r);
  const button = () => $("#bkReplaced").querySelector(`button[data-replaced="${CSS.escape(r.name)}"]`);
  sayIn($("#bkReplacedMsg"), "");
  if (!await ask({title: `Delete ${what}?`, action: "Delete now", danger: true,
      message: [`It's deleted from the data folder now, rather than ${r.deletes <= Date.now() / 1000 ? "within the hour" : "on " + when(r.deletes)}, and can't be put back.`,
                "The live data and the backups stay as they are" + (bkLeft(r) ? ": the backup it came from still has it." : ".")]})) { bkBack(button); return; }
  let st;
  try { st = await fresh(() => call("backups/replaced/" + encodeURIComponent(r.name), "DELETE"), $("#bkReplacedReauth")); }
  catch (e) { say("#bkReplacedMsg", `Not deleted: ${e.why || e.message}`, true); bkBack(button); return; }
  const at = (BK.replaced || []).findIndex(x => x.name === r.name);
  drawBackups(st);
  say("#bkReplacedMsg", `Deleted ${what}.`);
  const next = $("#bkReplaced").querySelectorAll("button[data-replaced]");   // the next one's Delete now, or the heading
  (next[Math.min(at, next.length - 1)] || $("#bkRestoreH")).focus();
}
