/* Sanguity · activity logs (sharing.js, admin.js): what each entry says, and a log that opens on demand. A profile's log
   (GET profiles/<id>/activity) on the Sharing page, under Activity (#168); the install's account events (GET activity)
   on the Admin page. Uses the page's own call() and when() (sharing.js, admin.js). */
"use strict";
const WHAT = {
  view: () => "opened the records",
  document: d => `opened ${d.name || "a document"}`,
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
  export: () => "exported everything",
  summary: d => `made a visit summary${d.for ? ` for the appointment on ${d.for}` : ""}`,
  upload: d => d.source === "smart-health-link" ? `imported a SMART Health Link from ${d.host}` : `uploaded ${d.files} file${d.files === 1 ? "" : "s"} to import`,
  outside: d => `had Sanguity contact ${d.host} (${d.purpose})`,
  discard: () => "cancelled an import at its preview",
  undo: d => `undid an import${d.label ? ` (${d.label})` : ""}, removing what it brought`,
  edit: d => d.action === "cost" ? `marked ${d.of === "claim" ? "a claim's share" : "an out-of-pocket payment"}` + [   // #55
      "hsa" in d && (d.hsa ? ` as paid with an ${d.hsa}` : d.hsa === "" ? " as paid with neither an HSA nor an FSA" : " to be tagged from what it says"),
      "claim" in d && ({claim: " as paying a claim", none: " as paying no claim"}[d.claim] || " to be matched again"),
      "same" in d && (d.same === false ? " as a payment of its own" : " to be checked for duplicates again")].filter(Boolean).join(",") :
    d.action === "same" || d.action === "not_same" ? (d.undone ? `undid a decision on two results of “${d.title || "a test"}”`
    : d.action === "same" ? `linked two results of “${d.title || "a test"}” from different sources as the same result`
    : `marked two results of “${d.title || "a test"}” from different sources as not the same`) :
    (d.undone ? "undid an edit of" : ({hide: "hid", rename: "renamed", date: "changed the date of", not_stay: "marked as not a hospital stay",
    merge: "merged another event into"}[d.action] || "edited")) + ` “${d.title || "an event"}”` + (d.to && !d.undone ? ` to “${d.to}”` : ""),
  connect: d => d.change === "options" ? `changed what ${d.source} pulls` + (d.kinds ? ` (${d.kinds.join(", ")})` : d.kinds === null ? " (everything allowed)" : "") + (d.since ? ` from ${d.since}` : "")
    : d.change === "warning" ? `noted that ${d.text.replace(/\.$/, "").replace(/^./, s => s.toLowerCase())}`
    : d.change === "remove" ? `removed the ${(d.label || d.kind).toLowerCase()} ${d.source} brought (undoable from the import history)`
    : d.change === "fetch" ? `fetched the attachment “${d.title}” from ${d.source}`
    : `${d.reconnect ? "signed in again to" : "connected"} ${d.source}` + (d.kinds ? ` (${d.kinds.join(", ")})` : ""),
  disconnect: d => d.ended ? `lost access to ${d.source} (the vendor ended it)` : `disconnected ${d.source}` + (d.removed ? `, removing ${d.removed} import${d.removed === 1 ? "" : "s"}` : ""),
  journal: d => `${{add: "added", change: "changed", delete: "deleted"}[d.action] || "changed"} a journal ${({symptom: "symptom", side_effect: "side effect"})[d.kind] || "note"}`,
  source: d => d.named ? `renamed the import “${d.label}”` : `went back to calling an import “${d.label}”`,   // #38
  settings: d => d.section ? `reset the ${d.label || d.section} settings to their defaults`
    : d.reset ? `reset “${d.label || d.setting}” to its default` : `changed the setting “${d.label || d.setting}”`,
  "watch.secret": d => d.rotated ? "made a new watch receiver secret (the old one stopped working)" : "made the watch receiver secret",
  "settings.user": d => d.section ? `reset their own ${d.label || d.section} settings` : `changed their own setting “${d.label || d.setting}”${d.reset ? " back to its default" : ""}`,
  "settings.install": d => d.section ? `reset the server's ${d.label || d.section} settings` : `changed the server setting “${d.label || d.setting}”${d.reset ? " back to its default" : ""}`,
  calendar: d => ({create: `made a calendar feed (${d.details === "full" ? "full" : "minimal"} details)`, rotate: "gave their calendar feed a new address",
    change: `changed their calendar feed to ${d.details === "full" ? "full" : "minimal"} details${d.labs ? ", with expected labs" : ", without expected labs"}`,
    revoke: d.username ? `turned off ${d.username}’s calendar feed` : "turned off their calendar feed"}[d.change] || "changed a calendar feed"),
  link: d => d.change === "view" ? `opened the share link${d.label ? ` “${d.label}”` : ""}`   // #47, js/share-links.js
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
  "security.notice_dismissed": () => "dismissed the failed sign-ins notice",
  "connect.source": d => `${{add: "set up", edit: "changed", remove: "removed"}[d.change] || "changed"} the connected source “${d.name}”`,
  "passkey.add": d => `added the passkey “${d.name}”`,
  "passkey.remove": d => `removed the passkey “${d.name}”`,
  "settings.require_2fa": d => d.on ? "required two-factor sign-in for everyone" : "stopped requiring two-factor sign-in",
  "account.remove": d => `removed the account ${d.username}` +
    (d.handed_over && d.handed_over.length ? `, handing ${d.handed_over.join(", ")} to someone else` : ""),
  "profile.repair": d => `made ${d.username} owner of “${d.name}”, which had no active owner`,
  "profile.delete": d => `deleted the profile “${d.name}”${d.members > 1 ? ` (shared with ${d.members - 1} other${d.members > 2 ? "s" : ""})` : ""}`,
  "labmap.change": d => `changed the lab name match for “${d.name}” from ${d.from ? `“${d.from}”` : "its own series"} to ${d.to ? `“${d.to}”` : "its own series"}`,
  "labmap.remove": d => `removed the lab name match for “${d.name}”${d.target ? ` (to “${d.target}”)` : ""}`,
  // backups (#200): made (Back up now, or the schedule), checked, failing, the schedule changed
  "backup.create": d => `made a backup, ${d.name}` + (d.removed ? `, removing ${d.removed} older one${d.removed === 1 ? "" : "s"}` : ""),
  "backup.check": d => `checked the backup ${d.name}: ${d.ok ? "good" : "not good"}`,
  "backup.fail": d => `couldn't make a backup (${d.error})`,
  "backup.schedule": d => d.schedule === "off" ? "turned off scheduled backups" : `scheduled backups ${d.schedule}, keeping the newest ${d.keep}`,
};
const FILTERS = [["", "Everything"], ["view", "Opened the records"], ["document", "Opened documents"],
  ["share", "Sharing"], ["unshare", "Removed access"], ["rename", "Renames"], ["import", "Imports"], ["reload", "Reloads"],
  ["export", "Exports"], ["upload", "Uploads"], ["discard", "Cancelled imports"], ["undo", "Undone imports"], ["edit", "Edits"],
  ["outside", "Outside connections"], ["settings", "Settings"], ["connect", "Connected sources"], ["calendar", "Calendar feeds"],
  ["journal", "Journal"], ["summary", "Visit summaries"], ["source", "Renamed imports"], ["link", "Share links"]];
const describe = e => (WHAT[e.action] || (() => e.action))(e.detail || {}, e);
function activityRows(list) {
  return list.map(e => el("tr", {},
    el("td", {class: "nowrap", text: when(e.at)}), el("td", {text: e.actor + (e.actor_removed ? " (removed)" : "")}), el("td", {text: describe(e)}),
    el("td", {class: "muted", text: e.ip || ""})));
}
/* a log that opens on demand (or at once, with open): newest first, filtered by person (profile logs) and kind,
   older entries on request */
function activityPanel(path, members, {open = false} = {}) {
  const box = el("div", {class: "activity", hidden: true});
  const who = members ? el("select", {"aria-label": "Person"}, el("option", {value: "", text: "Everyone"}),
    ...members.map(m => el("option", {value: m.user_id, text: m.username}))) : null;
  const what = members ? el("select", {"aria-label": "Kind of activity"}, ...FILTERS.map(([v, t]) => el("option", {value: v, text: t}))) : null;
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
