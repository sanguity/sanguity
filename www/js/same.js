/* Sanguity · the same lab result from two sources (#96, server/crossmatch.py).
   The records list the person's decisions as DATA.same = [{id, same, found, sides, by, at}] and the possible matches
   still undecided as DATA.possible = [{ref, a, b}], each side {imp, name, t, value, unit, row: [labitem event id,
   row index] | null}. A result linked as the same is already shown once by the server: its series lists the other
   sources under "same" ({point index: [{imp, link}]}), and its drawer result under "same" ([{row, imp, link, name}]).
   Owners and editors confirm or undo here; the page then reloads, as after an edit (drawer.js afterEdit).
   Plain script, loaded in order by index.html after sources.js; all files share one global scope. */
"use strict";
/* one chart per test: series with the same name and unit from different imports are drawn together, each point
   keeping its own import (p.imp) and the sources linked to it (p.same). Points need their ts already. */
function joinSeries(labs) {
  const out = [], by = new Map();
  for (const s of labs) {
    s.pts.forEach((p, n) => { p.imp = s.imp; p.same = (s.same && s.same[n]) || null; });
    const k = s.name + "\u001f" + (s.unit || "");
    const have = by.get(k);
    if (!have) { const c = {...s, imps: [s.imp]}; by.set(k, c); out.push(c); continue; }
    have.pts = have.pts.concat(s.pts); have.txt = (have.txt || []).concat(s.txt || []); have.imps.push(s.imp);
  }
  for (const s of out) if (s.imps.length > 1) { s.pts.sort((a, b) => a.ts - b.ts); s.n = s.pts.length; }
  return out;
}
/* "CSV lab results; FHIR records (same result)" for a point, as plain text */
function pointSources(s, p) {
  const imp = p.imp ?? s.imp;
  return [sourceText(imp), ...(p.same || []).map(x => sourceText(x.imp) + " (same result)")].filter(Boolean).join("; ");
}
const sameValue = x => `${x.value} ${x.unit || ""}`.trim();
const sameWhen = t => /^\d{4}-\d{2}-\d{2}$/.test(t) || /T(00:00|12:00):00$/.test(t) ? fmtD(t) : fmtDT(t);
async function sendSame(path, opts, msg, reopen) {
  sayIn(msg, "Saving…");
  try {
    const r = await api(`profiles/${PROFILE.id}/same${path}`, opts);
    if (!r.ok) { const j = await r.json().catch(() => ({})); sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save it (${r.status}).`, true); return; }
    afterEdit(reopen);
  } catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); }
}
/* the lab day an event's result is listed in, to reopen after a reload (the result itself may be gone by then) */
function dayOf(eid) {
  const day = DATA.events.find(e => e.k === "lab" && (DATA.details[e.id]?.ids || []).includes(eid));
  return day ? day.id : eid;
}
/* under a result in the drawer: the other sources of a linked row, and the possible matches of its rows */
function sameNotes(eid, d) {
  if (eid == null || !d) return null;
  const rows = d.rows || [], msg = el("p", {class: "hint edit-msg", role: "status"}), lines = [];
  const reopen = {event: state.selEv ?? dayOf(eid)};
  for (const x of d.same || []) {
    const row = rows[x.row];
    lines.push(el("div", {class: "edit-line same-line", "data-link": x.link},
      el("span", {}, el("b", {text: (row ? row[0] : x.name) || "Result"}), " · the same result also came from ", ...sourceNodes(x.imp)),
      canEdit() ? el("button", {class: "btn small", type: "button", text: "Undo link", "data-tip": "Show the two results apart again",
        onclick: () => sendSame("/" + x.link, {method: "DELETE"}, msg, reopen)}) : null));
  }
  for (const x of DATA.possible || []) for (const [me, other] of [[x.a, x.b], [x.b, x.a]]) {
    if (!me.row || me.row[0] !== eid) continue;
    const alt = {...reopen, alt: other.row ? dayOf(other.row[0]) : null};
    lines.push(el("div", {class: "edit-line same-line possible", "data-ref": x.ref},
      el("span", {}, el("b", {text: me.name}), ` ${sameValue(me)} · possibly the same result as ${other.name} ${sameValue(other)}, ${sameWhen(other.t)}, from `,
        ...sourceNodes(other.imp)),
      canEdit() ? el("button", {class: "btn small primary", type: "button", text: "Same result", "data-tip": "Show it once, with both sources",
        onclick: () => sendSame("", {method: "POST", body: JSON.stringify({ref: x.ref, same: true})}, msg, alt)}) : null,
      canEdit() ? el("button", {class: "btn small", type: "button", text: "Not the same", "data-tip": "Keep both, and stop flagging them",
        onclick: () => sendSame("", {method: "POST", body: JSON.stringify({ref: x.ref, same: false})}, msg, alt)}) : null));
  }
  if (!lines.length) return null;
  return el("div", {class: "edits same"}, el("div", {class: "sect", text: "From more than one source"}), ...lines, msg);
}
/* the decisions, for the list of every edit (drawer.js openEdits) */
function describeSame(x) {
  const [a, b] = x.sides || [];
  if (!a || !b) return x.same ? "Linked as the same result" : "Marked as not the same result";
  const side = s => `${s.name} ${sameValue(s)}${s.label ? " (" + s.label + ")" : ""}`;
  return `${x.same ? "Same result" : "Not the same result"}: ${side(a)} and ${side(b)}, ${sameWhen(a.t)}`;
}
function sameLines(msg) {
  return (DATA.same || []).map(x => el("div", {class: "edit-line"},
    el("span", {}, describeSame(x), x.by ? ` · by ${x.by}` : "", x.found ? null : el("em", {class: "missing", text: " · not found in the current records"})),
    canEdit() ? el("button", {class: "btn small", type: "button", text: "Undo", "data-link": x.id,
      onclick: () => sendSame("/" + x.id, {method: "DELETE"}, msg, {edits: true})}) : null));
}
