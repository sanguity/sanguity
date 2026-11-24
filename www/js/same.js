/* Sanguity · the same lab result from two sources (#96, server/crossmatch.py).
   The records list the person's decisions as DATA.same = [{id, same, found, sides, by, at}] and the possible matches
   still undecided as DATA.possible = [{ref, a, b}], each side {imp, name, t, value, unit, row: [labitem event id,
   row index] | null}. A result linked as the same is already shown once by the server: its series lists the other
   sources under "same" ({point index: [{imp, link}]}), and its drawer result under "same" ([{row, imp, link, name}]).
   Owners and editors confirm or undo here; the reply's records are then put in place, as after an edit (drawer.js afterEdit).
   Plain script, loaded in order by index.html after sources.js; all files share one global scope. */
"use strict";
/* one chart per test: series with the same name and unit from different imports are drawn together, each point
   keeping its own import (p.imp) and the sources linked to it (p.same). A result that came twice and isn't linked
   (#825: from two imports, or twice from one) is one point: the server gives each copy the one result's range and flag
   (crossmatch.copies, as the summary's), so a later copy at its time saying the same (its value as given, range,
   flag and edge) is left out, and the one kept names its import too (p.also: the Timeline counts it in each one's
   draw, util.js labDraws). Labs, the Overview, the heatmap and the rest read it once, its flag never under a copy's
   dot. The records' own series keep both, for each import's lab day (drawer.js rowPoint). Two the person said aren't
   the same result stay two (#833: the server names which result each is, p.apart from the series' "apart"), and at
   one time a flagged result comes after an unflagged one, so what reads the latest (the Overview's tile, a sparkline's
   dot) reads the flag. Points need their ts and ranges (applyRangeOverrides) already. */
function joinSeries(labs) {
  const out = [], by = new Map();
  for (const s of labs) {
    s.pts.forEach((p, n) => { p.imp = s.imp; p.same = (s.same && s.same[n]) || null; p.also = null; p.apart = s.apart?.[n] ?? null;
      p.rangeFrom = s.rangeFrom?.[n] ?? null; });   // (#834: the import whose copy's report gave its range, drawer.js resultTable)
    const k = s.name + "\u001f" + (s.unit || "");
    const have = by.get(k);
    if (!have) { const c = {...s, imps: [s.imp]}; by.set(k, c); out.push(c); continue; }
    have.pts = have.pts.concat(s.pts); have.txt = (have.txt || []).concat(s.txt || []); have.imps.push(s.imp);
  }
  for (const s of out) {
    if (s.imps.length > 1) s.pts.sort((a, b) => a.ts - b.ts || !!a[4] - !!b[4]);
    const at = new Map(), pts = s.watch ? s.pts : s.pts.filter(p => {   // at: the points kept at a time
      const there = at.get(p.ts), was = there?.find(q => isCopy(q, p));
      if (!there) at.set(p.ts, [p]); else if (!was) there.push(p);
      if (was) (was.also ||= []).push(p.imp);
      return !was;
    });
    if (s.imps.length > 1 || pts.length < s.pts.length) { s.pts = pts; s.n = pts.length; }
  }
  return out;
}
/* whether two results at one time are one's copies: the same time as given, value and value as given, range, flag and
   edge, and not two the person said aren't the same (#833) */
const isCopy = (a, b) => a[0] === b[0] && a[1] === b[1] && String(a[5] ?? "").trim() === String(b[5] ?? "").trim() && a[2] === b[2]
  && a[3] === b[3] && (a[4] || "") === (b[4] || "") && (a[7] || "") === (b[7] || "") && (a.rs || "") === (b.rs || "") && a.apart === b.apart;
/* a result shown once that came from more imports than its own (#825), in its row of Labs' table: " · also from 1 more
   source" (nothing for one that came twice from one) */
const alsoWords = p => { const n = new Set((p.also || []).filter(i => i !== p.imp)).size; return n ? ` · also from ${n} more source${n > 1 ? "s" : ""}` : ""; };
/* "CSV lab results; FHIR records (same result)" for a point, as plain text: its copies' imports too (#825) */
function pointSources(s, p) {
  const imp = p.imp ?? s.imp;
  return [...new Set([imp, ...(p.also || [])].map(sourceText)), ...(p.same || []).map(x => sourceText(x.imp) + " (same result)")].filter(Boolean).join("; ");
}
const sameValue = x => `${x.value} ${x.unit || ""}`.trim();
const sameWhen = t => /^\d{4}-\d{2}-\d{2}$/.test(t) || /T(00:00|12:00):00$/.test(t) ? fmtD(t) : fmtDT(t);
async function sendSame(path, opts, msg, reopen) {
  sayIn(msg, "Saving…");
  try {
    const r = await api(`profiles/${PROFILE.id}/same${path}`, opts);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save it (${r.status}).`, true); return; }
    afterEdit(reopen, j);
  } catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); }
}
/* the lab day an event's result is listed in, to reopen once the records are back (the result itself may be gone by then) */
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
