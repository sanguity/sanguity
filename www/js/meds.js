/* Sanguity · the medication timeline (#50): each medication as spans, with its dose changes and pharmacy fills.
   The records (GET api/profiles/{pid}/data) list them as DATA.medications = [{name, key, rxnorm, spans: [{from, to,
   open, stopped, dose, title, status, prescriber, sources, ids, rxnorm}], changes: [{t, from, to, id}], fills: [{t,
   id, title, pharmacy, supply}], start, stop}] (server/medications.py works them out from the med and rx events).
   On the Timeline the Medications lane draws one row per medication: a bar for each span (a new shade where the
   dose changes), a cap where the records say it stopped, an arrow where they give no end, and a tick for each fill.
   On Trends the same rows can be shown under every chart (the "Medications" tick box, off until ticked).
   It shows what the records say, never whether a dose or a gap is right. Every date goes through dates.js.
   Plain script, loaded in order by index.html after journal.js; all files share one global scope. */
"use strict";
let MEDS = [], medSel = null;   // the medications; the one open in the drawer (its index)

function medsLoad() {
  MEDS = (Array.isArray(DATA.medications) ? DATA.medications : []).filter(m => m && Array.isArray(m.spans) && Array.isArray(m.fills)).map((m, i) => {
    const spans = m.spans.filter(s => s && /^\d{4}-\d{2}-\d{2}$/.test(s.from || "")).map(s => ({...s, ts: dayTs(s.from), te: s.to ? dayTs(s.to) : null}));
    const fills = m.fills.filter(f => f && /^\d{4}-\d{2}-\d{2}$/.test(f.t || "")).map(f => ({...f, ts: byId.get(f.id)?.ts ?? dayTs(f.t)}));
    const words = [m.name, ...spans.flatMap(s => [s.title, s.dose, s.prescriber]), ...fills.map(f => f.title), ...(m.rxnorm || []), "medication"];
    return {...m, i, spans, fills, changes: Array.isArray(m.changes) ? m.changes : [], _q: words.filter(Boolean).join(" ").toLowerCase()};
  });
}
/* where a span ends on screen: its last day, or today when the records give no end */
const medEnd = s => s.te ?? Math.max(s.ts, Date.now());
const medFrom = m => Math.min(...m.spans.map(s => s.ts), ...m.fills.map(f => f.ts));
const medTo = m => Math.max(...m.spans.map(medEnd), ...m.fills.map(f => f.ts + (f.supply || 0) * DAY));
/* q: the Timeline's search; the Labs charts show every medication, whatever was searched there (#192) */
const medsShown = (a, b, q = state.q) => MEDS.filter(m => (m.spans.length || m.fills.length) && medTo(m) >= a && medFrom(m) <= b && (!q || m._q.includes(q)));
const medShort = (n, max) => n.length > max ? n.slice(0, max - 1) + "…" : n;
/* "5 Jan 2026 – 3 Mar 2026", "5 Jan 2026 – no end in the records", "stopped 5 Jan 2026 (no start or end date)" */
function medSpanWhen(s) {
  if (s.open) return `${fmtD(s.ts)} – no end in the records`;
  if (s.stopped && s.to === s.from) return `${fmtD(s.ts)} · listed as ${(s.status || "stopped").toLowerCase()}, no end date`;
  return `${fmtD(s.ts)} – ${fmtD(s.te)}`;
}
const medSpanSays = s => [s.title, s.dose].filter(Boolean).join(" · ");

/* ---------------------------------------------------------------- one row per medication, drawn by kind of mark */
const MED_ROW = 18;
function medRow(g, m, x, y, L, R, color, opts = {}) {
  const sel = medSel === m.i && opts.selectable && drawer.classList.contains("open");
  m.spans.forEach((s, n) => {
    const x1 = x(s.ts), x2 = x(medEnd(s));
    if (x2 < L || x1 > R) return;
    const a = Math.max(L, x1), b = Math.min(R, Math.max(x2, x1 + 3));
    const changed = n > 0 && m.changes.some(c => c.t === s.from);
    const attrs = {class: "medbar" + (opts.selectable ? " ev" : "") + (sel ? " sel" : ""), x: a, y: y - 4, width: Math.max(3, b - a), height: 8, rx: 2,
      fill: color, "fill-opacity": n % 2 ? .55 : .85, "data-med": m.i, "data-span": n};
    g.push(el("svg:rect", attrs));
    if (changed && x1 >= L && x1 <= R) g.push(el("svg:line", {class: "medchange", x1, x2: x1, y1: y - 7, y2: y + 7, "pointer-events": "none"}));
    if (s.stopped && x2 >= L && x2 <= R) g.push(el("svg:line", {class: "medstop", x1: b, x2: b, y1: y - 6, y2: y + 6, "pointer-events": "none"}));
    if (s.open && x2 <= R + 1) g.push(el("svg:path", {d: `M${b + 1},${y - 4}L${b + 6},${y}L${b + 1},${y + 4}Z`, fill: color, "fill-opacity": .6, "pointer-events": "none"}));
  });
  for (const f of m.fills) {
    const fx = x(f.ts); if (fx < L - 2 || fx > R + 2) continue;
    if (f.supply) { const fe = Math.min(R, x(f.ts + f.supply * DAY)); if (fe > fx + 1) g.push(el("svg:line", {class: "medsupply", x1: Math.max(L, fx), x2: fe, y1: y + 7, y2: y + 7, "pointer-events": "none"})); }
    g.push(el("svg:path", {class: "medfill" + (opts.selectable ? " ev" : "") + (state.selEv === f.id ? " sel" : ""), d: `M${fx},${y + 3}L${fx + 3.5},${y + 9}L${fx - 3.5},${y + 9}Z`,
      "data-id": opts.selectable ? f.id : null, "data-med": m.i, "data-fill": f.id}));
  }
}
/* the Timeline's Medications lane (timeline.js asks TL_LANES for its height and drawing) */
TL_LANES.med = {
  height: () => MEDS.length ? 20 + Math.max(1, medsShown(state.from, state.to).length) * MED_ROW : 0,
  draw(g, S, top, h, c, w) {
    const rows = medsShown(state.from, state.to), narrow = w < 600;
    g.push(el("svg:text", {class: "lane-label", x: 0, y: top + 15, text: c.label}));
    g.push(el("svg:line", {class: "gridline", x1: S.L, x2: S.R, y1: top + h, y2: top + h, "stroke-opacity": .5}));
    if (!rows.length) { g.push(el("svg:text", {class: "medname", x: S.L + 4, y: top + 20 + MED_ROW / 2 + 4, text: "No medications in this date range"})); return; }
    rows.forEach((m, n) => {
      const y = top + 20 + n * MED_ROW + MED_ROW / 2 - 2;
      g.push(el("svg:text", {class: "medname", x: 8, y: y + 4, text: medShort(m.name, narrow ? 13 : 22)}, el("svg:title", {text: m.name})));
      medRow(g, m, S.x, y, S.L, S.R, c.color, {selectable: true});
    });
  },
};
function medTip(m, ds, ev) {
  if (!m) return hideTip();
  if (ds.fill) { const e = byId.get(+ds.fill); if (e) return showEvTip(e, ev); }
  const s = m.spans[+ds.span]; if (!s) return hideTip();
  showTip([el("div", {class: "v", text: m.name}),
    el("div", {class: "s"}, el("span", {class: "k", style: {background: CAT_OF.med.color}}), medSpanWhen(s)),
    medSpanSays(s) && medSpanSays(s) !== m.name ? el("div", {class: "s", text: medSpanSays(s)}) : null,
    s.status ? el("div", {class: "s", text: "Status: " + s.status}) : null,
    s.prescriber ? el("div", {class: "s", text: "Prescribed by " + s.prescriber}) : null,
    m.fills.length ? el("div", {class: "s", text: `${m.fills.length} pharmacy fill${m.fills.length > 1 ? "s" : ""} in the records`}) : null], ev);
}

/* ---------------------------------------------------------------- Trends: the same rows under each chart, when ticked */
let medsOnTrends = false;
try { medsOnTrends = localStorage.getItem("sg-meds-trends") === "1"; } catch (e) {}
const medsInWindow = () => medsOnTrends && state.cats.has("med") ? medsShown(state.from, state.to, "") : [];
CHART_LAYERS.push({
  space: () => { const n = medsInWindow().length; return n ? 6 + n * 22 : 0; },
  draw(g, x, top, R) {
    medsInWindow().forEach((m, n) => {
      const y = top + 6 + n * 22 + 14;
      const first = Math.max(52, x(medFrom(m)));
      g.push(el("svg:text", {class: "medname", x: Math.min(first, R - 80) + 2, y: y - 7, text: medShort(m.name, 40)}));
      const before = g.length;
      medRow(g, m, x, y, 52, R, CAT_OF.med.color);
      for (const node of g.slice(before)) if (node.dataset?.med != null) {
        node.addEventListener("pointermove", ev => { ev.stopPropagation(); medTip(m, node.dataset, ev); });
        node.addEventListener("pointerleave", hideTip);
        node.addEventListener("click", ev => { ev.stopPropagation(); hideTip(); node.dataset.fill ? openEvent(byId.get(+node.dataset.fill)) : openMedication(m); });
        node.style.cursor = "pointer";
      }
    });
  },
});
{
  const marks = $("#optMarks")?.closest("label");
  if (marks) marks.after(el("label", {id: "optMedsLabel", class: "toggle"}, el("input", {type: "checkbox", id: "optMeds", checked: medsOnTrends || null, onchange: ev => {
    medsOnTrends = ev.target.checked; try { localStorage.setItem("sg-meds-trends", medsOnTrends ? "1" : "0"); } catch (e) {} renderCharts(); }}), " Medications"));
}

/* ---------------------------------------------------------------- the drawer: one medication's history */
function openMedication(m) {
  if (!m) return;
  medSel = m.i; state.selEv = null;
  const last = m.spans[m.spans.length - 1];
  $("#dkey").style.background = CAT_OF.med.color; $("#dtitle").textContent = m.name;
  $("#dsub").textContent = "Medication history · " + (m.spans.length ? `${fmtD(m.spans[0].ts)} – ${last.open ? "no end in the records" : fmtD(last.te)}` : `pharmacy fills only, from ${fmtD(m.fills[0].ts)}`);
  const body = $("#dbody"); body.replaceChildren(); body.scrollTop = 0; body.classList.remove("fill");
  const kv = (rows) => el("dl", {class: "kv"}, ...rows.filter(r => r[1]).flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: v})]));
  body.append(kv([["Started", m.spans.length ? fmtD(m.spans[0].ts) : ""], ["Stopped", m.stop ? fmtD(dayTs(m.stop)) : ""],
    ["Still listed", last && last.open ? "Yes: the latest listing gives no end date" : ""],
    ["Dose changes", m.changes.length ? String(m.changes.length) : ""], ["Pharmacy fills", m.fills.length ? String(m.fills.length) : ""],
    ["RxNorm", (m.rxnorm || []).join(", ")]]));
  if (m.spans.length) {
    body.append(el("div", {class: "sect", text: "What the records list"}), el("div", {class: "tablewrap", style: {maxHeight: "none"}}, el("table", {class: "data medspans"},
      el("thead", {}, el("tr", {}, ["When", "Listed as", "Status", "Prescribed by", "Source"].map(h => el("th", {text: h})))),
      el("tbody", {}, m.spans.map(s => el("tr", {class: "link", tabindex: 0, title: "Open this listing", onclick: () => openEvent(byId.get(s.ids[0])), onkeydown: k => { if (k.key === "Enter") { k.preventDefault(); openEvent(byId.get(s.ids[0])); } }},
        el("td", {text: medSpanWhen(s)}), el("td", {text: medSpanSays(s)}), el("td", {text: s.status || ""}), el("td", {text: s.prescriber || ""}),
        el("td", {text: (s.sources.length ? s.sources : [...new Set(s.ids.map(id => importName(byId.get(id)?.imp)).filter(Boolean))]).join(", ")})))))));
  }
  if (m.changes.length) body.append(el("div", {class: "sect", text: "Changes"}), el("ul", {class: "medchanges"},
    ...m.changes.map(c => el("li", {}, el("b", {text: fmtD(dayTs(c.t))}), ` · ${c.from} → ${c.to}`))));
  if (m.fills.length) {
    body.append(el("div", {class: "sect", text: "Pharmacy fills"}), el("div", {class: "tablewrap", style: {maxHeight: "none"}}, el("table", {class: "data medfills"},
      el("thead", {}, el("tr", {}, ["Date", "Drug", "Pharmacy", "Supply"].map(h => el("th", {text: h})))),
      el("tbody", {}, m.fills.map(f => el("tr", {class: "link", tabindex: 0, title: "Open this fill", onclick: () => openEvent(byId.get(f.id)), onkeydown: k => { if (k.key === "Enter") { k.preventDefault(); openEvent(byId.get(f.id)); } }},
        el("td", {text: fmtD(f.ts)}), el("td", {text: f.title}), el("td", {text: f.pharmacy || ""}), el("td", {text: f.supply ? `${f.supply} days` : ""})))))));
  }
  const notes = JOURNAL.filter(e => e.j.med && medKeyOf(e.j.med) === m.key);
  if (notes.length) body.append(el("div", {class: "sect", text: "Journal entries linked to it"}),
    ...notes.sort((a, b) => a.ts - b.ts).map(e => el("button", {class: "btn", type: "button", text: `${fmtD(e.ts)} · ${e.title}`, onclick: () => openEvent(e)})));
  body.append(el("p", {class: "hint", style: {color: "var(--ink2)"}, text: "What the records say, in order: a listing's dates may not be when a medication started or ended. This isn't medical advice."}),
    moreAbout("A clinic's medication list dates a medication from when it was recorded there, which may not be when it was started; a listing with no end date may still have ended."),
    el("div", {class: "btns"}, el("button", {class: "btn", type: "button", text: "Zoom timeline to this medication", onclick: () => {
      const a = medFrom(m), b = medTo(m), pad = Math.max(7 * DAY, (b - a) * .05); setRange(a - pad, b + pad); pickTab("timeline"); }})));
  showDrawer();
  renderTimeline();
}
/* the same grouping as server/medications.med_key: the leading words before any strength or form */
function medKeyOf(name) {
  name = String(name || "").split(/\s+/).join(" ").trim();
  const m = /[\d(\[]|\b(?:tab|tabs|tablet|tablets|cap|caps|capsule|capsules|oral|solution|injection|suspension|cream|ointment|patch|inhaler|er|xr|sr|dr|hcl|hydrochloride|sodium|calcium|potassium|mg|mcg|ml)\b/i.exec(name);
  const head = (m ? name.slice(0, m.index) : name).replace(/^[\s,;:\-/]+|[\s,;:\-/]+$/g, "").toLowerCase();
  return (head.match(/[a-z][a-z'-]*/g) || []).join(" ") || name.toLowerCase();
}
/* in a medication listing's or fill's drawer: its whole history */
function medHistoryAction(e) {
  if (!e || (e.k !== "med" && e.k !== "rx")) return null;
  const m = MEDS.find(m => m.spans.some(s => s.ids.includes(e.id)) || m.fills.some(f => f.id === e.id));
  return m ? el("div", {class: "btns"}, el("button", {class: "btn", type: "button", text: "Medication history", onclick: () => openMedication(m)})) : null;
}
