/* Sanguity · the Admin page's Backups (#200, server/backup_jobs.py): when the newest backup was made, how big it is
   and where it went, with a warning when it's late (on every group of the page, and beside Backups in the sidebar);
   Back up now and Check a backup, which run in the background while this polls how they go; and the schedule.
   What the backups take of the watch receiver's data, and a check's counts of it (#227).
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
function drawSchedule(st) {
  const form = $("#bkSchedule"), msg = el("span", {class: "msg", role: "status"});
  const opts = [["off", "Off"], ["daily", "Daily"], ["weekly", "Weekly"]];
  const keep = el("input", {type: "number", id: "bkKeep", min: 1, max: st.keep_max, step: 1, value: st.keep, class: "set-num", required: true});
  const save = el("button", {class: "btn primary small", type: "submit", text: "Save", disabled: true});
  const pick = () => form.querySelector("input[name=bkSched]:checked")?.value || st.schedule;
  const changed = () => { save.disabled = pick() === st.schedule && +keep.value === st.keep; sayIn(msg, ""); };
  keep.oninput = changed;
  form.replaceChildren(
    el("fieldset", {class: "set-radios"}, el("legend", {class: "set-sr", text: "Make a backup"}),
      el("div", {class: "seg wrap", id: "bkSchedSeg"}, ...opts.map(([v, l]) => el("label", {},
        el("input", {type: "radio", name: "bkSched", value: v, checked: v === st.schedule, onchange: changed}), l)))),
    el("label", {class: "bk-keep"}, "Keep the newest ", keep, " backups"),
    el("p", {class: "hint", text: "While a schedule is on, older backups in the folder are deleted after each new one, keeping " +
      "this many. Only Sanguity's own backup files are counted or deleted; anything else in the folder is left alone. The " +
      "app makes the backup when the newest is a day (or a week) old, whoever made it."}),
    el("div", {class: "row"}, save, msg));
  form.onsubmit = async ev => {
    ev.preventDefault();
    const body = {schedule: pick(), keep: +keep.value}, n = st.backups.length;
    const more = bkRemoves(n, body.schedule, body.keep) - bkRemoves(n, st.schedule, st.keep);
    if (body.schedule === "off" && st.schedule !== "off" && !await ask({title: "Turn off scheduled backups?",
        message: ["The app stops making backups by itself. The backups in the folder stay.",
                  "Only do this if something else makes them, like a job on the server."], action: "Turn off", danger: true})) return;
    if (more > 0 && !await ask({title: `Keep only the newest ${plural(body.keep, "backup")}?`,
        message: `The next backup then deletes ${more === 1 ? "the oldest backup" : `the ${more} oldest backups`} from the folder.`,
        action: "Keep fewer", danger: true})) return;
    save.disabled = true;
    try {
      const st2 = await fresh(() => call("backups/schedule", "PUT", body), $("#bkReauth"));
      drawBackups(st2);
      say("#bkNowMsg", body.schedule === "off" ? "Scheduled backups are off." : `Backups are scheduled ${body.schedule}, keeping the newest ${body.keep}.`);
    } catch (e) { sayIn(msg, e.message, true); changed(); }
  };
}

/* ---------------------------------------------------------------- the backups in the folder, and checking one */
const bkWhen = name => { const m = /(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)/.exec(name || "");
  return m ? when(new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000) : name; };
function drawList(st) {
  const box = $("#bkList");
  if (!st.backups.length) { box.replaceChildren(el("p", {class: "muted", text: "No backups in the folder yet."})); return; }
  const shown = bkShowAll ? st.backups : st.backups.slice(0, 5);
  box.replaceChildren(el("div", {class: "tbl-wrap"}, el("table", {class: "tbl bk-list"},
    el("thead", {}, el("tr", {}, ...["Made", "Size", "Last checked", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, ...shown.map(b => el("tr", {"data-backup": b.name},
      el("td", {}, when(b.at), el("div", {class: "muted bk-name", text: b.name})),
      el("td", {class: "nowrap", text: bkSize(b.size)}),
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
  const words = rows.map(([t, w]) => `${counts[t].backup.toLocaleString()} ${w}${counts[t].backup === 1 ? "" : "s"}`).join(", ");
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
        el("td", {text: counts[t].backup.toLocaleString()}),
        el("td", {text: counts[t].expected == null ? "not recorded" : counts[t].expected.toLocaleString()}),
        el("td", {text: t in now ? now[t].toLocaleString() : "—"})))))) : null,
    run.ok ? el("p", {class: "bk-watch", text: bkWatchChecked(w)}) : null,
    w && w.counts ? el("div", {class: "tbl-wrap"}, el("table", {class: "tbl bk-counts bk-watch-counts"},
      el("caption", {text: "Phone and watch data in the backup, and in the receiver's folder now"}),
      el("thead", {}, el("tr", {}, ...["", "Backup", "Expected", "Now"].map(h => el("th", {text: h})))),
      el("tbody", {}, ...BK_WATCH.filter(([k]) => k in w.counts).map(([k, label]) => el("tr", {},
        el("th", {scope: "row", text: label + "s"}),
        el("td", {text: w.counts[k].backup.toLocaleString()}),
        el("td", {text: w.counts[k].expected == null ? "not recorded" : w.counts[k].expected.toLocaleString()}),
        el("td", {text: w.now && k in w.now ? w.now[k].toLocaleString() : "—"})))))) : null));
}
/* the watch receiver's data in a good backup (#227): when the receiver had last saved it, and what came after */
function bkWatchChecked(w) {
  if (!w) return "It has no phone and watch data: there was none when it was made, or an older version made it.";
  const saved = w.saved ? `, as the receiver last saved it ${when(Date.parse(w.saved) / 1000)}` : "";
  return `Its phone and watch data, for ${plural(w.profiles, "profile")}, opens and is intact too${saved}` +
    (w.after ? `, with ${plural(w.after, "upload")} that came after that save (folded in when the receiver starts after a restore).` : ".");
}
