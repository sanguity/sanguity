/* Sanguity · activity logs (sharing.js, admin.js): what each entry says, and a log that opens on demand. A profile's log
   (GET profiles/<id>/activity) on the Sharing page, under Activity (#168); the install's account events (GET activity)
   in Settings' Install (#991). Uses the page's own call() and when() (sharing.js, settings.js). */
"use strict";
const KIND_NAME = {visit: "Visits", contact: "Chart activity", procedure: "Procedures", imaging: "Imaging", study: "Reports & studies",
  message: "Messages", appt_msg: "Appointment messages", auto_msg: "Automated messages", letter: "Letters", document: "Documents"};   // #237
/* a server setting's value in the install log (#1006): on or off, a list, a number or some text */
const setVal = v => v === true ? "on" : v === false ? "off" : Array.isArray(v) ? (v.length ? v.join(", ") : "none")
  : v === "" || v == null ? "none" : String(v);
const WHAT = {
  view: () => "opened the records",
  document: d => d.title ? `opened the document “${d.title}”` : `opened ${d.name || "a document"}`,   // its record's title (#342); before, its file's name
  create: () => "created the profile",
  rename: d => `renamed the profile from “${d.from}” to “${d.to}”`,
  share: d => d.repair ? `made ${d.username} an owner, as an admin (the profile had no active owner)` :
    d.from ? `changed ${d.username}’s role from ${d.from} to ${d.role}` : `shared it with ${d.username} as ${d.role}`,
  unshare: (d, e) => d.username === e.actor ? "left the profile" : `removed ${d.username}’s access`,
  import: d => d.counts ? `imported an upload${d.skipped && (d.skipped.events || d.skipped.results)
    ? `, skipping ${d.skipped.events + d.skipped.results} duplicate${d.skipped.events + d.skipped.results === 1 ? "" : "s"}` : ""}` +
    (d.labs && d.labs.matched ? `, matching ${d.labs.matched} lab name${d.labs.matched === 1 ? "" : "s"} to existing series` : "")
    : `imported records (${d.documents} documents)`,
  reload: () => "loaded the latest records",
  export: d => (d.format === "tax" ? `downloaded Costs' tax-year report for ${d.year}` : d.format === "fhir" ? "exported as FHIR" : "exported everything") + (d.cycle ? ", with the cycle records" : ""),   // #183, #205, #1136
  summary: d => `made a visit summary${d.for ? ` for the appointment on ${recordDate(d.for)}` : ""}${(d.sections || []).includes("cycle") ? ", with the cycle records" : ""}`,
  emergency: d => d.file === "png" ? "made an emergency card lock-screen image" : `made an emergency card PDF${d.paper === "a4" ? " (A4)" : ""}`,   // #1126
  cycle: d => ({on: "turned cycle tracking on", off: "turned cycle tracking off, deleting what was kept", delete: "deleted the cycle records kept"}[d.change] || "changed cycle tracking"),   // #183
  upload: d => d.source === "smart-health-link" ? `imported a SMART Health Link from ${d.host}` : `uploaded ${d.files} file${d.files === 1 ? "" : "s"} to import`,
  outside: d => `had Sanguity contact ${d.host} (${d.purpose})`,
  discard: () => "cancelled an import at its preview",
  // family history (#1134), never who or what
  family: d => ({add: "added a family history entry", back: "put back a family history entry", change: "changed a family history entry",
    delete: "removed a family history entry", hide: "hid a family history entry of the records", show: "showed a family history entry of the records again"}[d.change] || "changed the family history"),
  // a lab name match of the profile's own (#1097), never its names
  labmap: d => ({add: "matched a lab name to a test", keep: "kept a lab name as its own test", change: "changed a lab name match", check: "kept a lab name match to check",
    remove: "removed a lab name match", undo: "undid a change to a lab name match"}[d.change] || "changed a lab name match") + (d.moved ? `, moving ${d.moved} saved result${d.moved === 1 ? "" : "s"}` : ""),
  undo: d => `undid an import${d.label ? ` (${d.label})` : ""}, removing what it brought`,
  edit: d => d.action === "cost" && d.change ? `${{add: "added", change: "changed", remove: "removed"}[d.change] || "changed"} ${d.of === "bill" ? "a bill" : "a plan year"} on Costs` :   // #1127, #1128
    d.action === "cost" ? `marked ${d.of === "claim" ? "a claim's share" : "an out-of-pocket payment"}` + [   // #55
      "hsa" in d && (d.hsa ? ` as paid with an ${d.hsa}` : d.hsa === "" ? " as paid with neither an HSA nor an FSA" : " to be tagged from what it says"),
      "claim" in d && ({claim: " as paying a claim", none: " as paying no claim"}[d.claim] || " to be matched again"),
      "same" in d && (d.same === false ? " as a payment of its own" : " to be checked for duplicates again")].filter(Boolean).join(",") :
    d.action === "same" || d.action === "not_same" ? (d.undone ? `undid a decision on two results of “${d.title || "a test"}”`
    : d.action === "same" ? `linked two results of “${d.title || "a test"}” from different sources as the same result`
    : `marked two results of “${d.title || "a test"}” from different sources as not the same`) :
    (d.undone ? "undid an edit of" : ({hide: "hid", rename: "renamed", date: "changed the date of", not_stay: "marked as not a hospital stay",
    merge: "merged another event into", kind: "moved", kind_rule: "stopped a rule for"}[d.action] || "edited")) + ` “${d.title || "an event"}”`
    + (d.to && !d.undone ? (d.action === "kind" ? ` to ${KIND_NAME[d.to] || d.to}` : ` to “${d.to}”`) : "")
    + (d.similar ? ` and ${d.similar} similar ${d.similar === 1 ? "entry" : "entries"}` : "") + (d.rule ? ", and for new imports" : "")
    + (d.with ? ` (and ${d.with} made with it)` : ""),   // #237
  connect: d => d.change === "options" ? `changed what ${d.source} pulls` + (d.kinds ? ` (${d.kinds.join(", ")})` : d.kinds === null ? " (everything allowed)" : "") + (d.since ? ` from ${fmtD(d.since)}` : "")
    : d.change === "warning" ? `noted that ${d.text.replace(/\.$/, "").replace(/^./, s => s.toLowerCase())}`
    : d.change === "remove" ? `removed the ${(d.label || d.kind).toLowerCase()} ${d.source} brought (undoable from the import history)`
    : d.change === "fetch" ? `fetched the attachment “${d.title}” from ${d.source}`
    : `${d.reconnect ? "signed in again to" : "connected"} ${d.source}` + (d.kinds ? ` (${d.kinds.join(", ")})` : ""),
  disconnect: d => d.ended ? `lost access to ${d.source} (the vendor ended it)` : `disconnected ${d.source}` + (d.removed ? `, removing ${d.removed} import${d.removed === 1 ? "" : "s"}` : ""),
  followup: d => ({add: "noted a follow-up", done: "marked a follow-up done", undone: "marked a follow-up not done", delete: "removed a follow-up"}[d.change] || "changed a follow-up"),   // (#1138)
  request: d => ({add: "logged a records request", change: "changed a records request", link: "linked a records request to an import",
    unlink: "unlinked a records request from its import", delete: "removed a records request"}[d.change] || "changed a records request"),   // (#1133)
  journal: d => ({"spot.rename": "renamed a spot in the journal", "spot.merge": "merged two spots in the journal", "spot.untag": "took a journal photo off its spot"})[d.action] || `${{add: "added", change: "changed", delete: "deleted"}[d.action] || "changed"} a journal ${({symptom: "symptom", side_effect: "side effect", mood: "mood entry"})[d.kind] || "note"}`,   // mood: #161
  insurance: d => ({add: "added an insurance plan", change: "changed an insurance plan", delete: "removed an insurance plan",   // #1137
    photo: `put on a photo of an insurance card's ${d.side === "back" ? "back" : "front"}`,
    "photo.delete": `took off the photo of an insurance card's ${d.side === "back" ? "back" : "front"}`}[d.change] || "changed an insurance plan"),
  source: d => d.named ? `renamed the import “${d.label}”` : `went back to calling an import “${d.label}”`,   // #38
  settings: d => d.section ? `reset the ${d.label || d.section} settings to their defaults`
    : d.reset ? `reset “${d.label || d.setting}” to its default` : `changed the setting “${d.label || d.setting}”`,
  "watch.secret": d => d.ended ? `took away the watch receiver secret ${d.maker || "someone"} made, as they can no longer change this profile (their phone can't upload)`   // (#929)
    : d.rotated ? "made a new watch receiver secret (the old one stopped working)" : "made the watch receiver secret",
  "settings.user": d => d.section ? `reset their own ${d.label || d.section} settings` : `changed their own setting “${d.label || d.setting}”${d.reset ? " back to its default" : ""}`,
  "settings.install": d => d.section ? `reset the server's ${d.label || d.section} settings` + (d.changes?.length ? ` (${d.changes.map(c => `“${c.label}” from ${setVal(c.from)} to ${setVal(c.to)}`).join("; ")})` : "")
    : `${d.reset ? "put" : "changed"} the server setting “${d.label || d.setting}”${d.reset ? " back to its default" : ""}` + ("to" in d ? ` (from ${setVal(d.from)} to ${setVal(d.to)})` : ""),   // (#1006: its values)
  "server.restart": d => (d.fell_back ? "started with the settings before: the new ones didn't run healthy" : "restarted the server")   // (#1121)
    + (d.changes?.length ? ` (${d.changes.map(c => `“${c.label}” from ${setVal(c.from)} to ${setVal(c.to)}`).join("; ")})` : ""),
  calendar: d => ({create: `made a calendar feed (${d.details === "full" ? "full" : "minimal"} details)`, rotate: "gave their calendar feed a new address",
    change: `changed their calendar feed to ${d.details === "full" ? "full" : "minimal"} details${d.labs ? ", with expected labs" : ", without expected labs"}`,
    revoke: d.username ? `turned off ${d.username}’s calendar feed` : "turned off their calendar feed"}[d.change] || "changed a calendar feed"),
  link: d => d.change === "view" ? (d.all ? `showed all of ${d.all} on the share link` : "opened the share link") + (d.label ? ` “${d.label}”` : "")   // #47, js/share-links.js; #498: Show all
    : `${d.change === "revoke" ? "revoked" : "made"} the share link${d.label ? ` “${d.label}”` : ""}`,
  setup: () => "set up Sanguity with the first account",
  password: () => "changed their password",
  "account.create": d => `created the account ${d.username}${d.admin ? " (admin)" : ""}`,
  "account.disable": d => `disabled ${d.username}`,
  "account.enable": d => `enabled ${d.username}`,
  "account.admin": d => `made ${d.username} an admin`,
  "account.unadmin": d => `removed ${d.username}’s admin role`,
  "account.reset": d => `reset ${d.username}’s password`,
  "2fa.on": () => "turned on two-factor sign-in",
  "2fa.off": () => "turned off two-factor sign-in",
  "2fa.codes": () => "made new recovery codes",
  "2fa.recovery": d => `signed in with a recovery code (${d.left} left)`,
  "2fa.reset": d => `turned off two-factor sign-in for ${d.username}`,
  "2fa.stopped": d => `authenticator codes stopped after ${d.wrong} wrong ones in a row`,   // (#928)
  "security.notice_dismissed": () => "dismissed the failed sign-ins notice",
  "connect.source": d => `${{add: "set up", edit: "changed", remove: "removed"}[d.change] || "changed"} the connected source “${d.name}”`,
  "passkey.add": d => `added the passkey “${d.name}”`,
  "passkey.remove": d => `removed the passkey “${d.name}”`,
  "oidc.link": d => d.username ? `linked the sign-in identity “${d.name}” to ${d.username}` : `linked the sign-in identity “${d.name}”`,   // (#1109)
  "oidc.unlink": d => d.username ? `unlinked the sign-in identity “${d.name}” from ${d.username}` : `unlinked the sign-in identity “${d.name}”`,
  // notifications (#1130): turned on or off for a profile; the server's test, its token, and one that never went through
  notify: d => `turned ${d.on ? "on" : "off"} notifications of ${d.kind === "doses" ? "dose reminders" : "new results"}`,
  "notify.test": d => d.ok ? "sent a test notification" : `couldn't send a test notification (${d.why})`,
  "notify.token": d => d.change === "remove" ? "removed the notification server's access token" : "saved the notification server's access token",
  "notify.topic": () => "made a new notification topic of their own",
  "notify.fail": d => `couldn't send a notification of ${d.kind === "doses" ? "a dose due" : "new results"} after ${d.tries} tries (${d.why})`,
  "settings.require_2fa": d => d.on ? "required two-factor sign-in for everyone" : "stopped requiring two-factor sign-in",
  "account.remove": d => `removed the account ${d.username}` +
    (d.handed_over && d.handed_over.length ? `, handing ${d.handed_over.join(", ")} to someone else` : ""),
  "profile.repair": d => `made ${d.username} owner of “${d.name}”, which had no active owner`,
  "profile.delete": d => `deleted the profile “${d.name}”${d.members > 1 ? ` (shared with ${d.members - 1} other${d.members > 2 ? "s" : ""})` : ""}`,
  "labmap.change": d => d.kept ? `kept the lab name match for “${d.name}” → ${d.to ? `“${d.to}”` : "its own series"}`   // Keep (#575)
    : `changed the lab name match for “${d.name}” from ${d.from ? `“${d.from}”` : "its own series"} to ${d.to ? `“${d.to}”` : "its own series"}`,
  "labmap.remove": d => `removed the lab name match for “${d.name}”${d.target ? ` (to “${d.target}”)` : ""}`,
  "labmap.undo": d => `undid a change to the lab name match for “${d.name}”, back to ${d.target ? `“${d.target}”` : "its own series"}`,   // (#1114)
  // backups (#200): made (Back up now, or the schedule), checked, failing, the schedule changed
  "backup.create": d => `made a backup, ${d.name}` + (d.removed ? `, removing ${d.removed} older one${d.removed === 1 ? "" : "s"}` : ""),
  "backup.check": d => `checked the backup ${d.name}: ${d.ok ? "good" : "not good"}`,
  "backup.fail": d => `couldn't make a backup (${d.error})`,
  "backup.schedule": d => d.schedule === "off" ? "turned off scheduled backups" : `scheduled backups ${d.schedule}, keeping the newest ${d.keep}`,
  "backup.replaced": d => `deleted ${/^watch-restored-/.test(d.name) ? "the phone and watch data a restore left to move in" : "what a restore replaced"}, ${d.name}` + (d.days != null ? `, ${d.days} day${d.days === 1 ? "" : "s"} after the restore` : ""),   // (#603)
  // the provider directory's lists read by the server (#992): each list's count, or why it wasn't read
  "directory.update": d => {
    const name = id => ({"epic-brands": "Epic's", "oracle-millennium": "Oracle Health's"})[id] || id;
    return "read the provider directory's lists (" + [...Object.entries(d.read || {}).map(([id, n]) => `${name(id)}: ${n.toLocaleString("en-US")} entries`),
      ...Object.entries(d.failed || {}).map(([id, why]) => `${name(id)} not read: ${why}`)].join("; ") + ")";
  },
  // records saved before #1022 that start-up couldn't key as they are now (#1044): left as they were, tried again
  "records.rekey": d => `couldn't key ${(d.left || []).length} older record${(d.left || []).length === 1 ? "" : "s"} as they are now, so left them as they were: ${(d.left || []).join(", ")}`,
  // the copies #1022's bug made, one record each once keyed alike (#1068): the latest kept
  "records.copies": d => `made older copies of a connected source's changed records one record each, the latest kept: ${d.events || 0} record${d.events === 1 ? "" : "s"}, ${d.results || 0} lab result${d.results === 1 ? "" : "s"} and ${d.entries || 0} list entr${d.entries === 1 ? "y" : "ies"} removed, ${d.marks || 0} Costs mark${d.marks === 1 ? "" : "s"} moved`,
};
const FILTERS = [["", "Everything"], ["view", "Opened the records"], ["document", "Opened documents"],
  ["share", "Sharing"], ["unshare", "Removed access"], ["rename", "Renames"], ["import", "Imports"], ["reload", "Reloads"],
  ["export", "Exports"], ["upload", "Uploads"], ["discard", "Cancelled imports"], ["undo", "Undone imports"], ["edit", "Edits"],
  ["outside", "Outside connections"], ["settings", "Settings"], ["connect", "Connected sources"], ["calendar", "Calendar feeds"],
  ["journal", "Journal"], ["summary", "Visit summaries"], ["emergency", "Emergency cards"], ["source", "Renamed imports"], ["link", "Share links"], ["insurance", "Insurance cards"]];
const describe = e => (WHAT[e.action] || (() => e.action))(e.detail || {}, e);
function activityRows(list) {
  return list.map(e => el("tr", {},
    el("td", {class: "when"}, ...whenParts(e.at)), el("td", {text: e.actor + (e.actor_removed ? " (removed)" : "")}), el("td", {text: describe(e)}),
    el("td", {class: "muted"}, ...addressBreaks(e.ip || ""))));
}
/* when, its date and its time each kept whole, the line breaking between them only where it doesn't fit (app.css,
   #528: kept on one line, at tablet widths What was squeezed to a word a line) */
const whenParts = t => { const s = when(t), h = fmtTime(t * 1000), i = s.endsWith(h) ? s.length - h.length : 0;
  return i ? [el("span", {text: s.slice(0, i).trimEnd()}), " ", el("span", {text: h})] : [el("span", {text: s})]; };
/* an address, able to break after each colon of an IPv6 one, and nowhere else (app.css, #524: "127.0.0." / "1") */
const addressBreaks = ip => ip.split(/(?<=:)/).flatMap((p, i) => i ? [el("wbr"), p] : [p]);
/* a log that opens on demand (or at once, with open): newest first, filtered by person (profile logs) and kind,
   older entries on request */
function activityPanel(path, members, {open = false, cs = false} = {}) {   // cs: connected sources on (#1096): their kind offered
  const box = el("div", {class: "activity", hidden: true});
  const who = members ? el("select", {"aria-label": "Person"}, el("option", {value: "", text: "Everyone"}),
    ...members.map(m => el("option", {value: m.user_id, text: m.username}))) : null;
  const what = members ? el("select", {"aria-label": "Kind of activity"}, ...FILTERS.filter(([v]) => cs || v !== "connect").map(([v, t]) => el("option", {value: v, text: t}))) : null;
  const tbody = el("tbody"), more = el("button", {class: "btn small", type: "button", text: "Show older", hidden: true});
  let oldest = null;
  async function load(reset) {
    const q = new URLSearchParams({limit: 50});
    if (who && who.value) q.set("actor", who.value);
    if (what && what.value) q.set("action", what.value);
    if (!reset && oldest) q.set("before", oldest);
    const list = await call(`${path}?${q}`);
    if (reset) tbody.replaceChildren();
    tbody.append(...activityRows(list));
    if (reset && !list.length) tbody.append(el("tr", {}, el("td", {colspan: 4, class: "muted", text: "Nothing yet."})));
    oldest = list.length ? list[list.length - 1].id : oldest;
    more.hidden = list.length < 50;
  }
  for (const s of [who, what]) if (s) s.onchange = () => load(true);
  more.onclick = () => load(false);
  box.append(...(members ? [el("div", {class: "row"}, who, what)] : []),
    el("table", {class: "tbl"}, el("thead", {}, el("tr", {}, ...["When", "Who", "What", "Address"].map(h => el("th", {text: h})))), tbody), more);
  const toggle = el("button", {class: "btn small", type: "button", text: "Activity", "aria-expanded": "false",
    onclick: async () => { box.hidden = !box.hidden; toggle.setAttribute("aria-expanded", !box.hidden); if (!box.hidden) await load(true); }});
  if (open) { toggle.hidden = true; box.hidden = false; load(true); }
  return el("div", {class: "activity-wrap"}, toggle, box);
}
