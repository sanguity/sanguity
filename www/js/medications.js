/* Sanguity · the Medications tab (#275): "what is she taking now?", in one place. Four parts:
   - Today: each of today's doses of a current medication, logged with Taken, Taken late or Skipped (medlog.js's
     todayDoses: the dose log, #235, lives here; the Overview shows the same rows). A viewer sees what was logged.
   - Current: each medication whose latest listing gives no end (meds.js mrCurrent): since when the listings run
     unbroken, its latest listing's words, its last pharmacy fill, its reminders' times (#162). A row opens its drawer
     (meds.js openMedication: its history, reminders and log). A fill that came as a pharmacy claim too links to the
     claims on Costs, that drug's.
   - Past: the rest, closed until it's opened, then as it was left.
   - The medications over the records bar's period: a row each, drawn as the Timeline's Medications lane is (meds.js
     medRow: the listings, where they stopped, the fills, the doses logged), each mark opening what it is.
   It says what the records list, never whether a medication or a dose is right. Hideable like any tab (Settings →
   Tabs): Add → Log a dose then goes to the Overview's doses (app.js logDose).
   Plain script, loaded in order by index.html after medlog.js; all files share one global scope. */
"use strict";
/* where its listings have run unbroken to the latest (a gap of more than a day starts again) */
function medSince(m) {
  const s = m.spans;
  let i = s.length - 1;
  while (i > 0 && (s[i - 1].te == null || s[i - 1].te >= s[i].ts - DAY)) i--;
  return s[i].ts;
}
const medLastFill = m => m.fills.reduce((a, f) => !a || f.ts > a.ts ? f : a, null);
/* its latest listing's words, the name apart: "Take 1 tablet twice daily" */
const medListed = m => { const s = m.spans.at(-1); return s ? [s.title !== m.name && s.title, s.dose].filter(Boolean).join(" · ") : ""; };
/* its reminders in the calendar feed now: "8:00 AM and 8:00 PM every day"; "Off" when set but turned off */
function medReminds(m) {
  const rs = mrOf(m).map(r => [r, mrState(r, m).cls]);
  const on = rs.filter(([, c]) => c === "on").map(([r]) => `${mrAnd(r.times.map(mrTime))} ${mrDays(r.days)}`);
  return on.length ? on.join("; ") : rs.some(([, c]) => c === "off") ? "Off" : "None";
}
/* the drug's pharmacy claims, on Costs (#55): its claims listed there, in a period that has them. Its link's address
   names them (#452: #costs?drug=metformin, Costs' claims, the drug's), so one opened in a new window or tab (Ctrl, ⌘
   or Shift) is the browser's, and opens on them too (medCostsArrive) */
const medClaims = m => (DATA.costs || []).filter(c => c.kind === "pharmacy" && c.d && medKeyOf(c.title) === m.key);
const medCostsHref = m => "#costs?drug=" + encodeURIComponent(m.key);
function medCostsSet(m) {
  const ds = medClaims(m).map(c => dayTs(c.d));
  if (state.preset !== "all" && !ds.some(t => t >= state.from && t <= state.to)) periodVisit("all");   // for this visit: theirs stays saved (#475)
  // on Claims, whichever view Costs was left on (What you paid has no search), with only the filters that find the drug's
  if (typeof costState === "object") Object.assign(costState, {view: "claims", q: m.key, cat: "Pharmacy", deniedOnly: false});
}
/* the address naming a drug's claims, as the page opens or the address changes (app.js): Costs set to show them,
   -> whether it named one, so that once Costs is drawn they're shown as a click shows them (medCostsShow, #456) */
function medCostsArrive() {
  const k = new URLSearchParams(location.hash.split("?")[1] || "").get("drug"), m = k && MEDS.find(x => x.key === k);
  if (!m) return false;
  if (state.preset !== "custom") setPeriod(state.preset);   // (its dates, before they're looked at)
  medCostsSet(m);
  return true;
}
/* the drug's claims in sight, the filter on them said at their top, focus in their search */
function medCostsShow() {
  const q = document.querySelector("#costTable input.search");
  if (q) { $("#costTable").scrollIntoView({block: "start"}); q.focus({preventScroll: true}); }
}
function medOnCosts(m, ev) {
  ev.stopPropagation();   // not the row's drawer
  if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey || ev.button) return;   // another window's or tab's: the browser's (#452)
  ev.preventDefault();
  medCostsSet(m);
  pickTab("costs");
  medCostsShow();
}
/* a list of medications, a row each opening its drawer (the name a button, for the keyboard): in columns under a
   header (seen, not said), each fact named for a screen reader; on a phone each row wraps, its facts' names shown
   (app.css). cells(m): its facts' values (text or nodes), in the order of heads */
function medRows(label, heads, list, cells) {
  return [el("div", {class: "med-row med-hd", "aria-hidden": "true"}, ["Medication", ...heads].map(h => el("span", {text: h}))),
    el("ul", {class: "med-rows", "aria-label": label}, list.map(m => el("li", {class: "med-row", "data-med-key": m.key, onclick: () => openMedication(m)},
      el("button", {class: "cell-btn", type: "button", text: m.name, "aria-label": `${m.name}: its history`}),
      ...cells(m).map((v, i) => el("span", {}, el("span", {class: "med-k", text: heads[i] + " "}), v)))))];
}

let medPastOpen = false;   // Past as it was left, kept while the tab is drawn again (a dose logged, the window resized)
/* the lane's window: the period's; where that ends at the records' last day (a preset or a milestone) and a dose was
   logged after it, on to that dose, so one logged today shows */
function medWindow() {
  const [a, b] = periodWindow([Math.min(...MEDS.map(medFrom)), Math.max(...MEDS.map(medTo))]);
  const last = DOSES.length ? DOSES.at(-1).ts : 0, toEnd = state.preset in PRESET_DAYS || !!msOf(state.preset);
  return [a, toEnd && last > b ? last + DAY / 2 : b];
}
function renderMedications() {
  const box = $("#view-medications");
  if (!MEDS.length) {   // (#199) what would be here, and a way to start
    box.replaceChildren(emptyState("Medications appear here from the records: what's listed now, what was before, and the doses logged.",
      canEdit() ? {href: `sources.html?profile=${PROFILE.id}#manual`, text: "Type in a medication"} : null));
    return;
  }
  const cur = MEDS.filter(mrCurrent), past = MEDS.filter(m => !mrCurrent(m)).sort((a, b) => medTo(b) - medTo(a));
  const costs = shownTabs().includes("costs");
  const head = el("h2", {text: "Today"});
  const today = todayDoses({id: "medToday", head});   // (none without a current medication)
  const now = el("div", {class: "card"}, el("h2", {text: `Current (${cur.length})`}),
    cur.length ? medRows("Current medications", ["Since", "As listed", "Last fill", "Reminders"], cur, m => {
      const f = medLastFill(m);
      return [fmtD(medSince(m)), medListed(m) || "Not given",
        [f ? fmtD(f.ts) + (f.supply ? ` · ${f.supply} days` : "") : "None in the records",
          ...(f && costs && medClaims(m).length ? [el("br"), el("a", {href: medCostsHref(m), class: "med-costs", onclick: ev => medOnCosts(m, ev)}, "Its claims on Costs")] : [])],
        medReminds(m)];
    }) : el("p", {class: "hint", text: "The records list no medication without an end."}));
  const before = past.length ? el("details", {class: "card med-past", open: medPastOpen || null, ontoggle: e => { medPastOpen = e.target.open; }},
    el("summary", {}, el("h2", {text: `Past (${past.length})`})),
    medRows("Past medications", ["When", "Last listed as", "Pharmacy fills"], past, m => {
      const s = m.spans.at(-1), f = medLastFill(m);
      return [s ? `${fmtD(m.spans[0].ts)} – ${s.te != null && s.to !== s.from ? fmtD(s.te) : "listed as " + (s.status || "stopped").toLowerCase()}`
          : `pharmacy fills, ${fmtD(m.fills[0].ts)} – ${fmtD(f.ts)}`,
        medListed(m) || "Not given", m.fills.length ? String(m.fills.length) : "None"];
    })) : null;
  // the medications over the period (All: from the first listing or fill to the last), as the Timeline's lane
  const [a, b] = medWindow(), when = periodLabel("all the records");
  const rows = medsShown(a, b, "");
  const svg = el("svg:svg", {class: "med-lane", role: "img", "aria-label": `The medications from ${fmtD(a)} to ${fmtD(b)}, a row each: the same as listed under Current and Past.`});
  // its heading the period ("The last 30 days", "Since diagnosis (Apr 14, 2025)", "2024"); its ⓘ named for what it's about (#447)
  const lane = el("div", {class: "card"}, el("div", {class: "set-hd"}, el("h2", {text: when[0].toUpperCase() + when.slice(1)}),
      rows.length ? aboutWords({title: "the medications lane"}, "A bar for each listing, a new shade where the dose changed; a line across its end where the records say it stopped, an arrow where they give no end; a small triangle under it for each pharmacy fill.",
        "Doses logged: ● taken, ◆ taken late, ○ skipped.") : null),
    rows.length ? [el("p", {class: "hint", text: "Point at a mark for what it is; click it to open it."}), svg]
      : el("p", {class: "hint", text: "No medications in this period."}));
  box.replaceChildren(...[today, now, before, lane].filter(Boolean),
    el("p", {class: "hint", text: "What the records list, in order: a listing's dates may not be when a medication started or ended. This isn't medical advice."}));
  if (rows.length) drawMedLane(svg, rows, a, b);
}

/* a row per medication, from a to b, the names in a column of their own as wide as they need, a third of the lane at
   most (cut to it), on the kit's time axis (chartkit.js, #300); its rows and axis growing with larger text (#633) */
function drawMedLane(svg, rows, a, b) {
  const w = Math.max(280, Math.round(svg.getBoundingClientRect().width) || 600), k = ckK(), ROW = 24 * k;
  const L = Math.min(Math.max(...rows.map(m => labelW(m.name, "medname"))) + 12, w * (w < 600 ? .34 : .25)), R = w - 8, top = 4, plotB = top + rows.length * ROW;
  const x = ckScale(a, b, L, R), tk = ckTimeTicks(a, b, x, w), g = [];
  svg.setAttribute("viewBox", `0 0 ${w} ${plotB + ckAxisH(tk.rows)}`); svg.setAttribute("height", plotB + ckAxisH(tk.rows));
  for (const t of tk.ticks) g.push(el("svg:line", {class: "gridline", x1: x(t.t), x2: x(t.t), y1: top, y2: plotB}));
  ckAxis(g, tk, L, R, plotB, plotB + 16);
  rows.forEach((m, n) => {
    const y = top + n * ROW + ROW / 2;
    g.push(el("svg:text", {class: "medname", x: 0, y: y + 4 * k, text: ckFit(m.name, "medname", L - 8)}, el("svg:title", {text: m.name})));
    medRow(g, m, x, y, L, R, CAT_OF.med.color, {selectable: true});
  });
  svg.replaceChildren(...g);
}
{
  const svgOf = ev => ev.target.closest?.("svg.med-lane");
  const view = $("#view-medications");
  view.addEventListener("pointermove", ev => { if (!svgOf(ev)) return; const ds = ev.target.dataset || {}; ds.med != null ? medTip(MEDS[+ds.med], ds, ev) : hideTip(); });
  view.addEventListener("pointerout", ev => { if (svgOf(ev) && !svgOf({target: ev.relatedTarget || view})) hideTip(); });
  view.addEventListener("click", ev => {
    const ds = svgOf(ev) && ev.target.dataset;
    if (!ds || ds.med == null) return;
    hideTip();
    ds.fill ? openEvent(byId.get(+ds.fill)) : openMedication(MEDS[+ds.med]);
  });
}
