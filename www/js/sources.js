/* Sanguity · where each record came from (#100): the import that brought it, with a link to it in the import history.
   The records (GET api/profiles/{pid}/data) list their imports once, DATA.imports = {id: {label, origin, format, at,
   synced}}, and each event and lab series names its own as "imp" (a series' results are all of its import). "synced"
   is when a live connection (#91) last brought the import up to date: empty for anything read once.
   Plain script, loaded in order by index.html after core.js; all files share one global scope. */
"use strict";
const sourceOf = imp => imp == null || !DATA || !DATA.imports ? null : DATA.imports[imp] || null;
const importHref = imp => `import.html?profile=${PROFILE ? PROFILE.id : ""}#import-${imp}`;
/* "C-CDA health summary, imported 3 Nov 2026" (and its last sync), as plain text for tooltips */
function sourceText(imp) {
  const s = sourceOf(imp); if (!s) return "";
  return `${s.label}, imported ${fmtD(s.at * 1000)}` + (s.synced ? ` · last synced ${fmtDT(s.synced * 1000)}` : "");
}
/* one import as a link to the history, then when */
function sourceNodes(imp, note) {
  const s = sourceOf(imp); if (!s) return [];
  return [el("a", {href: importHref(imp), class: "src-link", "data-import": imp, title: "Open this import in the import history", text: s.label}),
    ` · imported ${fmtD(s.at * 1000)}`, s.synced ? ` · last synced ${fmtDT(s.synced * 1000)}` : null, note ? ` (${note})` : null];
}
/* the drawer's Source line for an event: its import, the import of anything merged into it, and whether it was edited */
function eventSource(e) {
  if (!e || e.imp == null) return null;
  const d = DATA.details[e.id] || {};
  const merged = (Array.isArray(d.merged) ? d.merged : []).map(m => m && m.imp).filter(i => i != null && sourceOf(i));
  const parts = [sourceNodes(e.imp), ...merged.map(i => ["; ", ...sourceNodes(i, "merged in")])];
  if (!parts[0].length) return null;
  const edited = (DATA.edits || []).some(x => x.found && x.ids.includes(e.id));
  return el("p", {class: "source"}, el("b", {text: "Source "}), ...parts.flat(),
    edited ? el("span", {class: "tag", text: "Edited"}) : null);
}
/* the line under a lab series' table on Trends */
function seriesSource(s) {
  if (!s || s.watch || !sourceOf(s.imp)) return null;
  return el("p", {class: "source"}, el("b", {text: "Source "}), ...sourceNodes(s.imp));
}
