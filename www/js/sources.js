/* Sanguity · where each record came from (#100): the import that brought it, with a link to it in the import history.
   The records (GET api/profiles/{pid}/data) list their imports once, DATA.imports = {id: {label, origin, format, at,
   synced}}, and each event and lab series names its own as "imp" (a series' results are all of its import). "synced"
   is when a live connection (#91) last brought the import up to date: empty for anything read once.
   Plain script, loaded in order by index.html after core.js; all files share one global scope. */
"use strict";
const sourceOf = imp => imp == null || !DATA || !DATA.imports ? null : DATA.imports[imp] || null;
/* what an import is called: its own name (#38, rename.js), else its importer's label */
const importName = imp => { const s = sourceOf(imp); return s ? s.name || s.label : ""; };
const importHref = imp => `sources.html?profile=${PROFILE ? PROFILE.id : ""}#import-${imp}`;
/* "C-CDA health summary, imported 3 Nov 2026" (and its last sync), as plain text for tooltips */
function sourceText(imp) {
  const s = sourceOf(imp); if (!s) return "";
  return `${importName(imp)}${s.name ? ` (${s.label})` : ""}, imported ${fmtD(s.at * 1000)}` + (s.synced ? ` · last synced ${fmtDT(s.synced * 1000)}` : "");
}
/* one import as a link to the history, then when */
function sourceNodes(imp, note) {
  const s = sourceOf(imp); if (!s) return [];
  return [el("a", {href: importHref(imp), class: "src-link", "data-import": imp, text: importName(imp),
      "data-tip": (s.name ? `In the records: ${s.label}. ` : "") + "Open this import in the import history"}),
    s.name ? el("span", {class: "muted", text: ` (${s.label})`}) : null, renameButton("import", imp),
    ` · imported ${fmtD(s.at * 1000)}`, s.synced ? ` · last synced ${fmtDT(s.synced * 1000)}` : null, note ? ` (${note})` : null];
}
/* the drawer's Source line for an event: its import, the import of anything merged into it or linked to it as the
   same result (#96), whom the record itself names as its source (once, here: #270), and whether it was edited
   (tag false: the drawer says so above it, with its edits, #406) */
function eventSource(e, tag = true) {
  if (!e || e.imp == null) return null;
  const d = DATA.details[e.id] || {}, own = typeof d.src === "string" && d.src !== "Typed in" && d.src !== importName(e.imp) ? d.src : "";
  const merged = (Array.isArray(d.merged) ? d.merged : []).map(m => m && m.imp).filter(i => i != null && sourceOf(i));
  const same = [...new Set((Array.isArray(d.same) ? d.same : []).map(x => x && x.imp))].filter(i => i != null && i !== e.imp && sourceOf(i));
  const parts = [sourceNodes(e.imp), ...merged.map(i => ["; ", ...sourceNodes(i, "merged in")]), ...same.map(i => ["; ", ...sourceNodes(i, "same result")])];
  if (!parts[0].length) return null;
  const edited = (DATA.edits || []).some(x => x.found && x.ids.includes(e.id));
  return el("p", {class: "source"}, el("b", {text: "Source "}), ...parts.flat(), own ? ` · as recorded: ${own}` : null,
    edited && tag ? el("span", {class: "tag", text: "Edited"}) : null);
}
/* the line under a lab series' table on Trends */
function seriesSource(s) {
  if (!s || s.watch) return null;
  const imps = [...new Set([...(s.imps || [s.imp]), ...s.pts.flatMap(p => (p.same || []).map(x => x.imp))])].filter(i => sourceOf(i));
  if (!imps.length) return null;
  return el("p", {class: "source"}, el("b", {text: imps.length > 1 ? "Sources " : "Source "}), ...imps.flatMap((i, n) => [n ? "; " : null, ...sourceNodes(i)]));
}
