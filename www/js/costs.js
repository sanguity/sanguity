/* Sanguity · Costs tab: claims and out-of-pocket costs (typed in, exported, or a claim's share; #55).
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- costs */
/* the period is the records bar's (#171, core.js; "All" is every claim on file). by: the chart's bars, "month" or
   "year" ("": by year once the claims shown span more than four years). view: the claims, or "paid", what was paid
   out of pocket (#278); more: a view's More figures open */
const costState = {q: "", cat: "", deniedOnly: false, sort: "d", dir: -1, limit: 200, by: "", seen: null, view: "claims", more: false};

/* a summary tile (#260): the one tile component (app.css), its label, its figure and a line under it, a date in it
   kept on one line (#330); a figure to read, not a button */
const statTile = (label, value, sub, id) => el("div", {class: "tile", id: id || null},
  el("span", {class: "lbl", text: label}), el("span", {class: "val", text: value}), sub ? el("span", {class: "delta"}, keepDates(sub)) : null);
/* a view's figures (#278): three to read first, the rest (and after them, more) under More figures, open or shut as
   last left; in a card, as the Overview's are, so the tiles are tinted, not bordered like the tiles that open
   something (#442); top: what comes first in it, a view's heading (What you paid's, so the h3 in its More figures
   follows its h2: #448). The three in one row, or one under another where a third is too narrow (.stats.three,
   app.css), as on the Overview */
function costFigures(box, head, rest, more, top = []) {
  const card = el("div", {class: "card cost-figs"}, ...top, threeFigures(head));
  if (rest.length || more) card.append(el("details", {class: "more cost-more", open: costState.more || null, ontoggle: e => { costState.more = e.target.open; }},
    el("summary", {text: "More figures"}), rest.length ? el("div", {class: "stats"}, ...rest) : null, more));
  box.append(card);
}
/* the three figures' grid, Costs' and the Overview's (#448): in a row where a third is wide enough (.stats.three,
   app.css) and each figure fits its tile, else one under another (.one, #456: "1 002 666 $US" in a French browser is
   wider than a third of the Overview's column at 1280 px). Measured as its width changes and once the fonts are in */
const threeFit = g => {
  g.classList.remove("one");
  if (g.clientWidth && [...g.children].some(t => t.scrollWidth > t.clientWidth + 1)) g.classList.add("one");
};
const threeSeen = new ResizeObserver(es => { for (const e of es) {
  const g = e.target, w = Math.round(e.contentRect.width);
  if (!g.isConnected) threeSeen.unobserve(g);
  else if (g.sgW !== w) { g.sgW = w; threeFit(g); }   // not again for its own height, which the fit changes
} });
document.fonts?.ready.then(() => document.querySelectorAll(".stats.three").forEach(threeFit));
function threeFigures(tiles) { const g = el("div", {class: "stats three"}, ...tiles); threeSeen.observe(g); return g; }
/* the claims' table, a claim per stacked row (.fold, app.css) where it's wider than its box (#465): measured, as the
   figures are, not at a fixed width (in French figures it needs 700–723 px, in English 660). A table wider than its
   box is at its narrowest, so that's the width it needs, kept as the box changes. Measured as soon as it's in the page
   (renderCosts), so it's stacked in the frame it's drawn, before anything scrolls to or focuses a claim in it (#467),
   and again as its box changes. Once the fonts are in, one not stacked is measured again (a stacked one isn't drawn
   as a table to measure it: that moved the page, #329) */
const claimsFit = w => {
  const t = w.firstElementChild, bw = w.getBoundingClientRect().width;
  if (!bw) return;   // not shown (another tab): measured when it is
  if (!t.sgNeed) { t.classList.remove("fold"); const tw = t.getBoundingClientRect().width; if (tw > bw) t.sgNeed = tw; }
  t.classList.toggle("fold", bw < t.sgNeed);
};
const claimsSeen = new ResizeObserver(es => { for (const e of es) {
  const w = e.target, bw = Math.round(e.contentRect.width);
  if (!w.isConnected) claimsSeen.unobserve(w);
  else if (w.sgW !== bw) { w.sgW = bw; claimsFit(w); }   // not again for its own height, which the fold changes
} });
document.fonts?.ready.then(() => document.querySelectorAll("table.claims:not(.fold)").forEach(t => { t.sgNeed = 0; claimsFit(t.parentElement); }));
function claimsFold(w) { claimsSeen.observe(w); return w; }
/* the claims' three figures, in this order wherever they're shown (#278, #442: Costs and the Overview): your share,
   what insurance paid and what was billed; the Overview's without the line under each (sub: false) */
function claimFigures(rows, sub = true) {
  const billed = sumOf(rows, "billed"), paid = sumOf(rows, "paid"), owe = sumOf(rows, "owe");
  return [statTile("Your share", usd(owe), sub && "per the insurers, before your payments"),
    statTile("Paid by insurance", usd(paid), sub && billed ? `${Math.round(paid / billed * 100)}% of billed` : ""),
    statTile("Billed by providers", usd(billed), sub && "list prices, before insurance")];
}
function costRows() { return inPeriod(DATA.costs || []); }
/* where the claims came from, one wording (#1163): imported from the insurer's files (each insurer's name and months),
   a health plan's connection, or typed in */
function claimsFrom(rows) {
  const g = {file: [], sync: [], manual: []};
  for (const r of rows) { const o = sourceOf(byId.get(r.id)?.imp)?.origin; (g[o] || g.file).push(r); }
  const span = xs => (t => t ? ` (${t})` : "")(insurerSpans(xs).map(([n, a, b]) => `${n}, ${monthYear(a)} – ${monthYear(b)}`).join("; "));
  const parts = [g.file.length && "from your insurer's claims you imported" + span(g.file), g.sync.length && "from your health plan's connection" + span(g.sync),
    g.manual.length && "typed in by you"].filter(Boolean);
  return parts.length ? (s => s[0].toUpperCase() + s.slice(1) + ".")(listOf(parts)) : "";
}
/* a table that stacks where its box is narrow (#443): each row its first cell, then the others, each named by its
   column (labelCells, util.js; table.stack, app.css), so no Costs table scrolls sideways. n: the table or its box */
const stacked = n => { const t = n.matches("table") ? n : n.querySelector("table"); t.classList.add("stack"); labelCells(t); return n; };
const isDenied = r => /DENIED/i.test(r.status || "");
const sumOf = (rows, k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
/* Costs drawn, its tables' headings measured as they stick over the rows (#476): a table's --th-h, the cells' scroll
   margin (app.css), so focus moved up a column isn't left under headings wrapped to two or three lines (63 px at
   667 x 375). Measured as soon as they're in the page, as the claims' fold is, before the Close or a row's date takes
   focus, and again as their size changes (the window turned, the fonts in) */
const thSeen = new ResizeObserver(es => { for (const e of es) costTh(e.target); });
const costTh = h => { if (h.isConnected) h.parentElement.style.setProperty("--th-h", h.offsetHeight + "px"); else thSeen.unobserve(h); };
function renderCosts() {
  drawCosts();
  for (const h of $("#view-costs").querySelectorAll("table.data thead")) { costTh(h); thSeen.observe(h); }
}
/* #199, #1163: what would be here and every real way in: claims files to upload (named), a payments file, a claim or
   a payment typed in (each its own form), a bill (#1128), a health plan's connection only while connected sources are
   on; and that nothing connects to a bank. The ways in only for owners and editors */
function costsEmpty() {
  const link = (hash, text) => el("a", {class: "empty-next", href: `sources.html?profile=${PROFILE.id}${hash}`, text});   // each a next step, as an empty state's are
  const es = emptyState("No claims or payments yet. Costs shows your insurance claims and what you paid for care.", null);
  if (canEdit()) es.append(el("p", {text: "Ways to add them:"}), el("ul", {class: "costs-ways"},
    el("li", {}, link("#upload", "Upload claims files"), ": UnitedHealthcare or Blue Cross Blue Shield claims.json, an EOB spreadsheet (CSV), or Blue Button (FHIR) claims from your insurer's website."),
    el("li", {}, link("#upload", "Upload a payments file"), ": a CSV of what you paid (date, payee, amount), downloaded yourself from where you keep track."),
    el("li", {}, link("#claim", "Type in a claim"), " · ", link("#payment", "Type in a payment")),
    el("li", {}, el("button", {class: "btn empty-next needs-server", type: "button", id: "billFirst", text: "Add a bill",
      onclick: () => { billState.first = true; billState.edit = "new"; renderCosts(); $("#bill-service")?.focus(); }})),
    sgConnected(ME) ? el("li", {}, link("#portals", "Connect your health plan"), " (experimental)") : null));
  es.append(el("p", {class: "hint", text: "Sanguity doesn't connect to banks, cards or HSA accounts: payments are what you type in or upload."}));
  return es;
}
function drawCosts() {
  const box = $("#view-costs"); box.replaceChildren();
  const claims = !!(DATA.costs || []).length;
  if (!claims && !((DATA.outOfPocket || {}).payments || []).length && !((DATA.oop || {}).bills || []).length && !billState.first) {
    box.append(costsEmpty());
    return;
  }
  // a new period starts at the top of the lists, with no payment open
  const seen = [state.preset, state.from, state.to].join();
  if (costState.seen !== seen) { costState.seen = seen; costState.limit = 200; paidState.q = ""; paidState.open = null; paidState.limit = 200; }
  const rows = costRows();
  const periodText = periodLabel(claims ? "all claims on file" : "everything on file");
  if (!claims) { box.append(...present(costLinks(false))); renderPaid(box, rows, periodText); return; }   // payments only: no claims to chart or list
  // Claims | What you paid (#278): the insurance story or the money out of pocket, each with its own figures
  box.append(el("div", {class: "toolbar"},
    // named for what it shows, as Labs' and the Timeline's are; the view in the address (#costs-paid, app.js), Back
    // returning to the one before (#442), and kept for the next visit (save(), core.js; #456)
    el("div", {class: "seg", id: "costView", role: "group", "aria-label": "Show the costs as"}, ...[["claims", "Claims"], ["paid", "What you paid"]].map(([k, l]) =>
      el("button", {type: "button", "aria-pressed": costState.view === k, text: l, onclick: () => { if (costState.view !== k) { costState.view = k; hideTip(); keepFocus(renderCosts); save(); tabAddress(true); } }}))),
    costState.view === "paid" ? null : el("span", {class: "hint", style: {margin: 0}, text: claimsFrom(DATA.costs)})));   // where the claims came from (#1163)
  box.append(...present(costLinks(true)));
  if (costState.view === "paid") { renderPaid(box, rows, periodText); return; }
  const billed = sumOf(rows, "billed"), paid = sumOf(rows, "paid"), owe = sumOf(rows, "owe");
  const denied = rows.filter(isDenied).length;
  const tile = statTile;
  costFigures(box, claimFigures(rows),
  [tile("Discounts & not covered", usd(Math.max(0, billed - paid - owe)), "network discounts, write-offs, denials"),
    tile("Claims", rows.length.toLocaleString("en-US"), `${rows.filter(r => r.kind === "pharmacy").length} pharmacy fills`),
    tile("Denied", denied.toLocaleString("en-US"), denied ? `${rows.filter(r => /PROVIDER_DENIED/i.test(r.status)).length} provider-denied` : "")]);
  // the chart, by month or by year
  const ds = rows.map(r => r.d).sort(), span = ds.length ? (+ds.at(-1).slice(0, 4) - +ds[0].slice(0, 4)) * 12 + (+ds.at(-1).slice(5, 7) - +ds[0].slice(5, 7)) + 1 : 0;
  const by = costState.by || (span > 48 ? "year" : "month");
  // every Costs control draws the tab again with focus kept on it (store.js keepFocus), or given back to what it
  // belongs to, never left on <body> (#448)
  const mc = el("div", {class: "card"}, el("div", {class: "list-hd"}, el("h2", {text: `Insurance claims by ${by} · ${periodText}`}), el("span", {class: "spacer"}),
      el("div", {class: "seg", id: "costBy", role: "group", "aria-label": "Group the claims"}, ...[["month", "By month"], ["year", "By year"]].map(([k, l]) =>
        el("button", {type: "button", "aria-pressed": by === k, text: l, onclick: () => { costState.by = k; keepFocus(renderCosts); }}))),
      viewSeg("#costBars", "the claims by " + by, renderCosts)));   // Chart | Table (#60)
  box.append(mc);
  if (state.tables.has("#costBars")) mc.append(stacked(chartTable([by === "year" ? "Year" : "Month", "Insurance paid", "Your share", "Billed", "Claims"],
    [...costBuckets(rows, by)].reverse().map(([k, m]) => [by === "year" ? k : zdate(dayTs(k + "-15")).toLocaleDateString(undefined, {month: "long", year: "numeric"}),
      usd(m.paid), usd(m.owe), usd(m.billed), m.n.toLocaleString("en-US")]))));
  else {
    const leg = ckLegend([["var(--measure)", "Your share"], [MUTED, "Paid by insurance"], ["var(--stay)", "Hospital stay", "stay-key"]]);   // a stay's edge too (#450)
    leg.classList.add("mb-2"); mc.append(leg);
    const svg = chartSvg(by === "year" ? "Yearly insurance costs" : "Monthly insurance costs", {id: "costChart"}); mc.append(svg);   // a tab stop as it's made (#652)
    requestAnimationFrame(() => drawCostBars(svg, rows, by));
  }
  box.append(planCard());   // the deductible and out-of-pocket max, per plan year (#1127): under the chart, which stays on the first screen
  // category + provider breakdowns
  const two = el("div", {class: "ov-cols"});
  const catCard = el("div", {class: "card"}, el("h2", {text: "By type of care"}), el("p", {class: "hint", text: "Grouped from billing codes on each claim. Click a row to list those claims below."}));
  const provCard = el("div", {class: "card"}, el("h2", {text: "Top providers"}), el("p", {class: "hint", text: "Ranked by total cost (insurance paid + your share)."}));
  two.append(catCard, provCard); box.append(two);
  barList(catCard, groupCosts(rows, r => r.cat), k => { costState.cat = costState.cat === k ? "" : k; toClaims(); }, costState.cat);
  barList(provCard, groupCosts(rows, r => r.prov || "(unknown)").slice(0, 10), k => { costState.q = k; toClaims(); });
  // pharmacy
  const rx = rows.filter(r => r.kind === "pharmacy");
  if (rx.length) {
    const g = groupCosts(rx, r => r.title.replace(/\s+\d.*$/, ""));
    const pc = el("div", {class: "card"}, el("h2", {text: `Prescriptions (${rx.length} fills)`}));
    pc.append(scrollBox({class: "tablewrap", "aria-label": "Prescriptions, by drug"}, stacked(el("table", {class: "data"},   // in the page's scroll (#456)
      el("thead", {}, el("tr", {}, ["Drug", "Fills", "First", "Last", "Insurance paid", "Your share"].map((h, i) => el("th", {class: i === 1 || i > 3 ? "num" : null, text: h})))),
      el("tbody", {}, g.map(x => el("tr", {class: "link", onclick: () => { costState.q = x.key; costState.cat = "Pharmacy"; toClaims(); }},
        el("td", {}, el("button", {class: "cell-btn", type: "button", text: x.key})), el("td", {class: "num", text: x.n}), el("td", {text: fmtD(dayTs(x.first))}), el("td", {text: fmtD(dayTs(x.last))}),
        el("td", {class: "num", text: usd(x.paid)}), el("td", {class: "num", text: usd(x.owe)}))))))));
    box.append(pc);
  }
  // claims table
  const tc = el("div", {class: "card", id: "costTable"});
  const cats = [...new Set(rows.map(r => r.cat))].sort();
  let list = rows.filter(r => (!costState.cat || r.cat === costState.cat) && (!costState.deniedOnly || isDenied(r)) &&
    (!costState.q || `${r.title} ${r.prov} ${r.cat} ${r.ins}`.toLowerCase().includes(costState.q.toLowerCase())));
  const k = costState.sort, dir = costState.dir;
  list.sort((a, b) => (typeof a[k] === "number" ? (a[k] - b[k]) : String(a[k]).localeCompare(String(b[k]))) * dir);
  const q = el("input", {class: "search", type: "search", placeholder: "Search service, provider, drug…", "aria-label": "Search claims", value: costState.q});
  q.oninput = () => { clearTimeout(window._cq); window._cq = setTimeout(() => { costState.q = q.value; renderCosts(); const n = $("#costTable input.search"); n.focus(); n.setSelectionRange(n.value.length, n.value.length); }, 250); };
  const sel = el("select", {class: "search", "aria-label": "Type of care", style: {minWidth: "0"}, onchange: e => { costState.cat = e.target.value; keepFocus(renderCosts); }}, el("option", {value: "", text: "All types of care"}), ...cats.map(c => el("option", {value: c, text: c, selected: c === costState.cat || null})));
  const den = el("label", {class: "toggle"},   // a filter on or off: a toggle chip (#193, #353)
    el("input", {type: "checkbox", checked: costState.deniedOnly || null, onchange: e => { costState.deniedOnly = e.target.checked; keepFocus(renderCosts); }}), "Denied only");   // focus kept on it, drawn again (store.js)
  // with filters on: what they keep, with Reset filters, and the totals for the claims shown (#190, filters.js)
  const on = !!(costState.q || costState.cat || costState.deniedOnly);
  const totals = `${on ? "For the claims shown: billed" : `${list.length} claims · billed`} ${usd(sumOf(list, "billed"))} · insurance paid ${usd(sumOf(list, "paid"))} · your share ${usd(sumOf(list, "owe"))}`;
  tc.append(el("h2", {tabindex: -1, text: "Claims"}), el("div", {class: "toolbar", style: {marginTop: "8px"}}, q, sel, den),
    filterLine(on ? [`Showing ${list.length.toLocaleString("en-US")} of ${rows.length.toLocaleString("en-US")} claims`, costState.cat && `type of care: ${costState.cat}`,
      costState.q && `matching “${costState.q.trim()}”`, costState.deniedOnly && "denied only"] : [],
      () => { Object.assign(costState, {q: "", cat: "", deniedOnly: false}); renderCosts(); }, () => $("#costTable input.search")),
    el("p", {class: "hint", text: totals}));
  const cols = [["d", "Date"], ["title", "Service / drug"], ["prov", "Provider"], ["cat", "Type"], ["billed", "Billed"], ["paid", "Ins. paid"], ["owe", "Your share"], ["status", "Status"]];
  const money = ["billed", "paid", "owe"];   // right-aligned, in tabular figures (#278)
  // where the table doesn't fit its box (measured, claimsFold) each claim is a stacked row, its headers a row of sort
  // buttons (.claims, app.css; #278, #443, #465)
  tc.append(claimsFold(scrollBox({class: "tablewrap", "aria-label": "Claims"}, el("table", {class: "data claims"},
    // a column's sort is a button in its header, its order said by aria-sort; a claim opens from its name, a button, or
    // anywhere on its row by pointer (#202: by keyboard too, and said as what they are)
    el("thead", {}, el("tr", {}, cols.map(([key, h], i) => el("th", {scope: "col", class: money.includes(key) ? "num" : null, "aria-sort": costState.sort === key ? (costState.dir > 0 ? "ascending" : "descending") : null},
      el("button", {class: "cell-btn", type: "button", onclick: () => {
        costState.dir = costState.sort === key ? -costState.dir : (["d", "billed", "paid", "owe"].includes(key) ? -1 : 1); costState.sort = key; renderCosts();
        $(`#costTable th:nth-child(${i + 1}) button`)?.focus(); }},   // the order an arrow (#257): ▲ and ▼ say out of range
        h, costState.sort === key ? [" ", icon(costState.dir > 0 ? "arrow-up" : "arrow-down")] : null))))),
    el("tbody", {}, list.slice(0, costState.limit).map(r => el("tr", {class: "link", onclick: () => openEvent(byId.get(r.id))},
      el("td", {text: fmtD(dayTs(r.d))}), el("td", {}, el("button", {class: "cell-btn", type: "button", text: r.title.length > 60 ? r.title.slice(0, 59) + "…" : r.title})),
      el("td", {text: r.prov}), el("td", {text: r.cat}),
      ...money.map(k => el("td", {class: "num", text: usd(r[k])})),
      // its status only when it isn't the usual one (#278): "processed", or "paid" from a FHIR plan
      el("td", {class: isDenied(r) ? "abn" : null, text: (s => /^(processed|paid)?$/.test(s) ? "" : s)((r.status || "").replace(/_/g, " ").toLowerCase())}))))))));
  if (list.length > costState.limit) tc.append(el("button", {class: "btn mt-2", type: "button", text: `Show ${Math.min(200, list.length - costState.limit)} more`,
    onclick: () => { const n = costState.limit; costState.limit += 200; renderCosts(); $(`#costTable tbody tr:nth-child(${n + 1}) .cell-btn`)?.focus(); }}));   // the first one added (#443)
  box.append(tc);
  claimsFit(tc.querySelector(".tablewrap"));   // stacked now, not a frame late (#467)
}
/* ---- out of pocket (#55): payments typed in or from a payments file uploaded, and claims' shares with no payment.
   Where each amount came from, the HSA/FSA tags and the payment-to-claim matches are worked out by the server
   (server/oop.py, DATA.oop); this counts each once: a payment that arrived twice once, a claim's share only until a
   payment is matched to it. */
const paidState = {q: "", open: null, limit: 200};   // limit: the amounts listed, 200 more at a time like the claims (#201)
/* where a payment came from, one label each wherever it's said (#1163): its row, its editor, the hint (taxreport.py
   says the same in its CSV) */
const OOP_SRC = {typed: "Typed in by you", records: "Imported with your records"};
const usd2 = v => (v || 0).toLocaleString("en-US", {style: "currency", currency: "USD"});   // with its cents, as usd (#543)
function oopRows() {
  const pays = (DATA.outOfPocket || {}).payments || [], o = DATA.oop || {};
  const info = new Map((o.payments || []).map(x => [x.i, x]));
  const claimOf = new Map((DATA.costs || []).filter(c => c.id != null).map(c => [c.id, c]));
  const rows = [];
  pays.forEach((p, i) => {
    const x = info.get(i) || {i, key: p.key, src: /^manual:/.test(p.key || "") ? "typed" : "records", hsa: p.hsa || null};
    rows.push({type: "pay", d: p.d, who: p.payee || "", group: p.group || "Other", amount: p.amount || 0, counted: x.dup != null ? 0 : (p.amount || 0),
      src: x.src, hsa: x.hsa || null, hsaFrom: x.hsaFrom, key: x.key, i, dup: x.dup, claim: x.claim != null ? claimOf.get(x.claim) || null : null,
      match: x.match, receipt: x.receipt, marked: x.marked, account: p.account || "", memo: p.memo || "", est: p.est});
  });
  const shares = o.claims || (DATA.costs || []).filter(c => c.id != null && c.owe >= 0.01).map(c => ({id: c.id, owe: c.owe, counted: c.owe, hsa: null}));
  for (const s of shares) {
    const c = claimOf.get(s.id);
    if (!c || !s.counted) continue;
    rows.push({type: "claim", d: c.d, who: c.prov || "", group: c.kind === "pharmacy" ? "Pharmacy" : "Medical bills", amount: s.owe, counted: s.counted,
      src: "claim", hsa: s.hsa || null, claim: c, id: s.id, marked: s.marked, ins: c.ins || ""});
  }
  return rows;
}
function paidRows() { return inPeriod(oopRows()); }
const srcText = r => r.type === "claim" ? `Your share, per ${r.ins ? r.ins + "'s" : "the"} claim; no payment recorded`
  : (OOP_SRC[r.src] || OOP_SRC.records) + (r.est ? " (estimated)" : "");
const oopKey = r => r.type === "claim" ? "c" + r.id : "p" + r.i;
async function sendMark(body, msg) {
  sayIn(msg, "Saving…");
  try {
    const r = await api(`profiles/${PROFILE.id}/costs/mark`, {method: "PUT", body: JSON.stringify(body)});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save (${r.status}).`, true); return; }
    storePut("oop", j);   // a mark changes only the out-of-pocket part, which its reply carries (store.js, #405)
    const again = document.querySelector("#oopEdit .oop-msg");
    sayIn(again, "Saved.");
  } catch (e) { sayIn(msg, "Couldn't reach the server: " + e.message, true); }
}
function oopEditor(r) {
  const msg = el("p", {class: "hint oop-msg"});   // shown and said by sayIn (util.js, #209)
  const box = el("div", {class: "oop-edit", id: "oopEdit"});
  box.append(el("h3", {class: "sub", text: r.type === "claim" ? `Your share of “${r.claim.title}”, ${fmtD(dayTs(r.d))}`
    : `${usd2(r.amount)} to ${r.who || "(no payee)"}, ${fmtD(dayTs(r.d))}`}));
  const lines = [];
  if (r.type === "pay") {
    lines.push(`Where the number came from: ${srcText(r).toLowerCase()}${r.account ? ", paid with " + r.account : ""}${r.memo ? " (" + r.memo + ")" : ""}.`);
    if (r.dup != null) {
      const o = oopRows().find(x => x.type === "pay" && x.i === r.dup);
      lines.push(`Counted once: it looks like the ${o ? srcText(o).toLowerCase() : "other"} payment${o ? " of " + fmtD(dayTs(o.d)) : ""}, which is counted instead.`);
    }
    if (r.claim) lines.push(`Paid the claim “${r.claim.title}” of ${fmtD(dayTs(r.claim.d))} (your share ${usd2(r.claim.owe)}), ${r.match === "auto" ? "matched by amount, date and provider" : "matched by hand"}; that share isn't counted again.`);
    else if (r.counted) lines.push("Not matched to a claim.");
    if (r.hsa) lines.push(`Tagged ${r.hsa}${r.hsaFrom === "account" ? " from what it was paid with" : r.hsaFrom === "typed" ? " when it was typed in" : ""}.`);
  } else lines.push(`Where the number came from: the claim's patient responsibility${r.ins ? ", as " + r.ins + " reported it" : ""}. It's counted until a payment is matched to the claim.`);
  for (const l of lines) box.append(el("p", {class: "hint", style: {margin: "2px 0"}, text: l}));
  const btns = el("div", {class: "btns my-2"});
  if (r.receipt != null && byId.get(r.receipt)) btns.append(el("button", {class: "btn", type: "button", text: "Open receipt", onclick: () => openEvent(byId.get(r.receipt))}));
  if (r.claim && byId.get(r.claim.id)) btns.append(el("button", {class: "btn", type: "button", text: "Open claim", onclick: () => openEvent(byId.get(r.claim.id))}));
  // closed, focus goes back to the row's date, the button that opened it (#448); its row filtered out since (a row
  // above or HSA only picked, the editor under the list then), to what's picked, else the list (#455), never <body>
  btns.append(el("button", {class: "btn", type: "button", text: "Close", onclick: () => { paidState.open = null; renderCosts();
    ($(`#paidTable tr[data-oop="${oopKey(r)}"] .cell-btn`) || $("#paidCard [aria-pressed=true]") || $("#paidTable")).focus(); }}));
  box.append(btns);
  if (canEdit()) {
    const target = r.type === "claim" ? {event: r.id} : {payment: r.key};
    const now = r.type === "claim" ? (r.hsa || "auto") : r.hsaFrom === "marked" ? (r.hsa || "none") : "auto";
    const opts = r.type === "claim" ? [["auto", "Not tagged"], ["HSA", "HSA"], ["FSA", "FSA"]]
      : [["auto", r.hsaFrom === "account" || r.hsaFrom === "typed" ? `As ${r.hsaFrom === "typed" ? "typed in" : "its paid with says"} (${r.hsa})` : "Not tagged"], ["HSA", "HSA"], ["FSA", "FSA"], ["none", "Neither"]];
    const hsa = el("select", {id: "oopHsaSel"}, ...opts.map(([v, t]) => el("option", {value: v, text: t, selected: v === now || null})));
    const form = el("div", {class: "oop-form needs-server"}, el("label", {for: "oopHsaSel"}, el("span", {text: "Paid with"}), hsa));   // Save and its fields: off offline (offline.js, #449)
    let claim = null;
    if (r.type === "pay") {
      const near = ((DATA.oop || {}).claims || []).map(s => (DATA.costs || []).find(c => c.id === s.id)).filter(Boolean)
        .sort((a, b) => Math.abs(dayTs(a.d) - dayTs(r.d)) - Math.abs(dayTs(b.d) - dayTs(r.d))).slice(0, 40);
      const linked = r.claim && r.match !== "auto" ? String(r.claim.id) : r.marked && !r.claim && r.counted ? "" : "-1";
      claim = el("select", {id: "oopClaimSel"}, el("option", {value: "-1", text: r.match === "auto" ? "Work it out (matched now)" : "Work it out"}),
        el("option", {value: "0", text: "No claim", selected: linked === "" || null}),
        ...near.map(c => el("option", {value: String(c.id), selected: linked === String(c.id) || null,
          text: `${fmtD(dayTs(c.d))} · ${c.prov || c.title} · your share ${usd2(c.owe)}`})));
      form.append(el("label", {for: "oopClaimSel"}, el("span", {text: "Paid the claim"}), claim));
    }
    form.append(el("button", {class: "btn primary", type: "button", text: "Save", onclick: () => {
      const body = {...target, hsa: hsa.value};
      if (claim) body.claim = +claim.value;
      sendMark(body, msg);
    }}));
    if (r.dup != null) form.append(el("button", {class: "btn", type: "button", text: "These are two payments", onclick: () => sendMark({...target, same: false}, msg)}));
    box.append(form);
  }
  box.append(msg);
  return box;
}
function renderPaid(box, claimRows, periodLabel) {
  const all = oopRows(), claims = !!(DATA.costs || []).length;
  const card = el("div", {class: "card", id: "paidCard"});
  // the view's heading and its ⓘ, named for what it's about, not the period (#447)
  const h2 = el("div", {class: "set-hd"}, el("h2", {text: `Out of pocket · ${periodLabel}`}));
  if (!all.length) {
    card.append(h2, el("p", {class: "hint"}, "No out-of-pocket payments yet, and no claim lists a share for you to pay. ",
      canEdit() ? el("a", {href: `sources.html?profile=${PROFILE.id}#payment`, text: "Type in a payment"}) : null));
    box.append(...present(card, billCard())); return;
  }
  const rows = paidRows(), counted = rows.filter(r => r.counted);
  const sum = xs => xs.reduce((a, r) => a + r.counted, 0);
  const pays = counted.filter(r => r.type === "pay"), shares = counted.filter(r => r.type === "claim");
  const has = s => all.some(r => r.src === s);
  // each source: its name, what the ⓘ adds, and its shorter name where all three are named (one line at 390 px, #467)
  const ways = [has("typed") && ["payments typed in", ""], has("records") && ["payments imported with your records", "", "imported"],
    has("claim") && ["claims' shares", " with no payment matched", "claims"]].filter(Boolean);
  const from = whole => "From " + (xs => xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs.at(-1))(ways.map(w => whole ? w[0] + w[1] : ways.length > 2 && w[2] || w[0])) + ".";
  // the view's heading, its figures under it in their card (#448); its hint one line, where the amounts came from, and
  // the rest behind its ⓘ: how each amount is counted (#280, #465)
  h2.append(aboutWords({title: "what you paid"}, from(true), "A payment that arrived two ways is counted once, and a claim's share only until a payment is matched to it. Click a row below to see where its number came from."));
  const top = [h2, el("p", {class: "hint", text: from(false)})];
  const tile = statTile;
  const typed = sum(pays.filter(r => r.src === "typed")), exp = sum(pays.filter(r => r.src !== "typed"));
  const prem = sum(counted.filter(r => /premium/i.test(r.group)));
  const hsa = sum(counted.filter(r => r.hsa === "HSA")), fsa = sum(counted.filter(r => r.hsa === "FSA"));
  const dups = rows.filter(r => r.dup != null).length, matched = pays.filter(r => r.claim).length;
  // the figures (#278): the total and its two parts first (with no claims, the HSA's), the rest under More figures
  const hsaTile = tile("Paid with an HSA", usd(hsa), "tagged HSA", "oopHsa");
  const head = [tile("Total out of pocket", usd(sum(counted)), `${pays.length} payment${pays.length === 1 ? "" : "s"}${shares.length ? ` + ${shares.length} claim share${shares.length === 1 ? "" : "s"}` : ""}`, "oopTotal"),
    tile("Payments", usd(sum(pays)), [typed && `${usd(typed)} typed in`, exp && `${usd(exp)} imported`].filter(Boolean).join("\u00a0· ") || "none in this period"),
    claims ? tile("Claim shares with no payment", usd(sum(shares)), "per the insurers; counted until a payment matches") : hsaTile];
  const tiles = [];
  if (claims) tiles.push(tile("Payments matched to claims", matched.toLocaleString("en-US"), dups ? `${dups} that arrived twice, counted once` : "by amount, date and provider, or by hand"), hsaTile);
  else if (dups) tiles.push(tile("Counted once", dups.toLocaleString("en-US"), "payments that arrived twice"));
  tiles.push(tile("Paid with an FSA", usd(fsa), "tagged FSA", "oopFsa"));
  if (prem) tiles.push(tile("Insurance premiums", usd(prem), counted.some(r => r.est && /premium/i.test(r.group)) ? "includes estimated months" : "premiums paid"));
  // HSA and FSA by year, under More figures too
  const years = new Map();
  for (const r of counted) if (r.hsa) {
    const y = years.get(r.d.slice(0, 4)) || {HSA: 0, FSA: 0, n: 0, rc: 0};
    y[r.hsa] += r.counted; y.n++; if (r.receipt != null) y.rc++;
    years.set(r.d.slice(0, 4), y);
  }
  costFigures(box, head, tiles, years.size ? el("div", {id: "oopTax", class: "mt-3"}, el("h3", {class: "sub", text: "HSA and FSA, by year"}),   // in the figures' card (#442), under its h2 (#448)
    scrollBox({class: "tablewrap", "aria-label": "HSA and FSA, by year"}, stacked(el("table", {class: "data"},   // stacked where narrow (#443)
      el("thead", {}, el("tr", {}, ["Year", "HSA", "FSA", "Amounts", "With a receipt"].map((h, i) => el("th", {class: i ? "num" : null, text: h})))),
      el("tbody", {}, [...years.entries()].sort((a, b) => b[0].localeCompare(a[0])).map(([y, v]) => el("tr", {},
        el("td", {text: y}), ...[usd2(v.HSA), usd2(v.FSA), v.n, v.rc].map(t => el("td", {class: "num", text: t}))))))))) : null, top);
  // cumulative: share listed vs bills paid. The view's cards each titled by an h2, as the claims' are (#442, #448)
  if (claims) {   // Chart | Table (#60): the table by month
    const bills = pays.filter(r => r.group === "Medical bills"), cum = el("div", {class: "card", id: "paidRun"});
    box.append(cum);
    cum.append(el("div", {class: "list-hd"}, el("h2", {text: "Your share and the bills you paid, running totals"}),
      el("span", {class: "spacer"}), viewSeg("#paidCum", "the running totals", renderCosts)));
    if (state.tables.has("#paidCum")) {
      const m = new Map();
      for (const [d, a, i] of [...claimRows.filter(r => r.owe).map(r => [r.d, r.owe, 0]), ...bills.map(r => [r.d, r.amount, 1])]) {
        const k = d.slice(0, 7), v = m.get(k) || [0, 0]; v[i] += a; m.set(k, v);
      }
      const acc = [0, 0], out = [...m.entries()].sort((p, q) => p[0].localeCompare(q[0])).map(([k, [s, p]]) => {
        acc[0] += s; acc[1] += p;
        return [zdate(dayTs(k + "-15")).toLocaleDateString(undefined, {month: "long", year: "numeric"}), usd(s), usd(p), usd(acc[0]), usd(acc[1]), usd(acc[0] - acc[1])];
      });
      cum.append(stacked(chartTable(["Month", "Your share listed", "Bills paid", "Share so far", "Paid so far", "Difference"], out.reverse())));
    } else {
      const svg = chartSvg("Your share and the bills you paid, running totals. Table lists them by month.", {id: "paidChart", height: 210});   // its height from the start, so what's under it doesn't move when it's drawn a frame later (#473); a tab stop (#652)
      const leg = ckLegend([["var(--measure)", "Your share per insurers, running total", "line"], [INK2, "Medical bills you paid, running total", "line"]]);
      leg.classList.add("mb-2"); cum.append(leg, svg);
      requestAnimationFrame(() => drawPaidCum(svg, claimRows, bills));
    }
  }
  // breakdowns, then every amount
  const two = el("div", {class: "ov-cols mt-3"});
  const g1 = el("div", {}, el("h3", {class: "sub", text: "What it was for"})), g2 = el("div", {}, el("h3", {class: "sub", text: "Who was paid"}));
  two.append(g1, g2); card.append(el("h2", {text: "Every amount out of pocket"}), two);
  const grp = keyFn => { const m = new Map(); for (const r of counted) { const k = keyFn(r), g = m.get(k) || {key: k, n: 0, v: 0}; g.n++; g.v += r.counted; m.set(k, g); } return [...m.values()].sort((a, b) => b.v - a.v); };
  // the list scrolled to, focus on its first control, in sight (HSA only, Reset filters or a date), not left on the
  // row above the screen (#462)
  const pick = k => { paidState.q = paidState.q === k ? "" : k; keepFocus(renderCosts); $("#paidTable").scrollIntoView({behavior: smoothly(), block: "nearest"});
    $("#paidTable :is(.toggle, .fsum-reset, .cell-btn)").focus({preventScroll: true}); };
  const bars = (host, groups) => {
    const max = Math.max(1, ...groups.map(g => g.v));
    // a row is a button, by keyboard too, saying whether it's picked, as the claims' are (#202, #448)
    // picked, tinted (in forced colours ringed, its bar in the text's colour: .cost-bar, app.css, #456)
    for (const g of groups) host.append(el("div", {class: "ov-row",
      ...pressable(() => pick(g.key)), "aria-pressed": paidState.q === g.key},
      el("span", {class: "n", text: g.key}), el("span", {class: "v", text: usd(g.v)}),
      el("span", {class: "s cost-bars"},   // the bar the row's width on one scale, its note under it, as the claims' (#483, #485)
        el("span", {class: "cost-bar-run"}, el("span", {class: "cost-bar", style: {width: Math.max(0, g.v / max * 100) + "%", background: INK2}})),
        el("span", {class: "cost-bar-note", text: `${g.n} amount${g.n === 1 ? "" : "s"}`}))));
  };
  bars(g1, grp(r => r.group)); bars(g2, grp(r => r.who || "(no payee)"));
  // every amount, with where it came from
  const fil = paidState.q;
  const list = rows.filter(r => !fil || r.group === fil || (r.who || "(no payee)") === fil || r.hsa === fil)
    .sort((a, b) => b.d.localeCompare(a.d) || a.type.localeCompare(b.type));
  const tw = el("div", {id: "paidTable", class: "mt-3", tabindex: -1});
  // filtered (HSA only, or a row above picked): what it keeps, with Reset filters, the total for those shown (#190)
  const kept = fil === "HSA" || fil === "FSA" ? `paid with an ${fil}` : rows.some(r => r.group === fil) ? `for ${fil}` : `paid to ${fil}`;
  tw.append(el("div", {class: "toolbar mb-2"},
    el("span", {class: "hint", style: {margin: 0}, text: fil ? `For the amounts shown: ${usd(sum(list))} counted` : `${list.length} amount${list.length === 1 ? "" : "s"} · ${usd(sum(list))} counted`}),
    ...["HSA", "FSA"].filter(t => all.some(r => r.hsa === t)).map(t => el("button", {class: "toggle", type: "button", "aria-pressed": fil === t, text: `${t} only`,   // on/off chips (#193)
      onclick: () => { paidState.q = fil === t ? "" : t; keepFocus(renderCosts); }}))),
    filterLine(fil ? [`Showing ${list.length.toLocaleString("en-US")} of ${rows.length.toLocaleString("en-US")} amounts`, kept] : [],
      () => { paidState.q = ""; renderCosts(); }, () => $("#paidTable .toggle") || $("#paidTable")));
  // opened, its row in sight first, then as much of the editor under it as fits: an editor taller than the window (a
  // sideways phone) lined up with its top would put the date that has focus above it (#473, 2.4.11), and clear of the
  // headings stuck over it, however many lines they wrap to (63 px at 667 px)
  const toggle = r => { paidState.open = paidState.open === oopKey(r) ? null : oopKey(r); renderCosts(); const e = $("#oopEdit");
    const b = e && $(`#paidTable tr[data-oop="${oopKey(r)}"] .cell-btn`), th = b?.closest("table").tHead.rows[0].cells[0];
    if (e) e.scrollIntoView({block: "nearest"});
    if (b) { b.scrollIntoView({block: "nearest"}); const over = (th.checkVisibility() ? th.getBoundingClientRect().bottom : 0) - b.getBoundingClientRect().top; if (over > -8) scrollBy(0, -over - 8); } };
  const shown = Math.max(paidState.limit, list.findIndex(r => oopKey(r) === paidState.open) + 1);   // the open one too
  const open = paidState.open && rows.find(r => oopKey(r) === paidState.open), edit = open && oopEditor(open);
  // the amounts in the page's own scroll, 200 at a time (Show more), as the claims are: no box that scrolls inside it
  // (#448; none on Costs, app.css, #456)
  tw.append(scrollBox({class: "tablewrap", "aria-label": "Every amount out of pocket"}, stacked(el("table", {class: "data oop"},
    el("thead", {}, el("tr", {}, ["Date", "Paid to", "For", "Where the number came from", "HSA/FSA", "Amount"].map((h, i) => el("th", {class: i === 5 ? "num" : null, text: h})))),
    // a row opens its editor in a row of its own under it (#448: not under the whole list), from anywhere on it by
    // pointer; its date is the button that says whether it's open (#202: a table's row can't be expanded itself)
    el("tbody", {}, list.slice(0, shown).flatMap(r => [el("tr", {class: "link" + (r.dup != null ? " oop-dup" : ""), "data-oop": oopKey(r), onclick: () => toggle(r)},
      el("td", {style: {whiteSpace: "nowrap"}}, el("button", {class: "cell-btn", type: "button", "aria-expanded": paidState.open === oopKey(r), "aria-controls": r === open ? "oopEdit" : null, text: fmtD(dayTs(r.d)),
        onclick: ev => { ev.stopPropagation(); toggle(r); $(`#paidTable tr[data-oop="${oopKey(r)}"] .cell-btn`)?.focus({preventScroll: true}); }})),
      el("td", {text: r.who || "—"}),
      el("td", {"data-tip": r.memo || null, text: r.type === "claim" ? `${r.group} · ${r.claim.title}` : r.group + (r.claim ? " · paid a claim" : "")}),
      el("td", {text: srcText(r) + (r.receipt != null ? " · receipt" : "")}),
      el("td", {text: r.hsa || ""}),
      el("td", {class: "num", style: {whiteSpace: "nowrap", color: r.amount < 0 ? "var(--good-text)" : null},
        text: (r.amount < 0 ? `${usd2(-r.amount)} refund` : usd2(r.amount)) + (r.dup != null ? " · counted once" : "")})),
      r === open ? el("tr", {class: "oop-open"}, el("td", {colspan: 6}, edit)) : null].filter(Boolean)))))));
  if (list.length > shown) tw.append(el("button", {class: "btn mt-2", type: "button", text: `Show ${Math.min(200, list.length - shown)} more`,
    onclick: () => { paidState.limit = shown + 200; renderCosts(); document.querySelectorAll("#paidTable tbody tr[data-oop] .cell-btn")[shown]?.focus(); }}));   // the first one added
  if (edit && !edit.parentElement) tw.append(edit);   // its row not listed (a row above picked since): under the list
  card.append(tw);
  box.append(...present(card, billCard(), taxCard()));   // the bills typed in, under every amount (#1128); the tax-year report (#1136)
}
/* ---- plan years (#1127): how much of the deductible and the out-of-pocket max the claims' shares have met, per plan
   year typed in here (owners and editors), else per calendar year with no limits. The sums are the server's
   (costplan.py, DATA.oop.years); arithmetic only, the numbers always as text, each bar their picture (aria-hidden),
   on the limit's scale */
const planState = {edit: null};   // the plan year being typed: "new", its id, or null
/* an amount typed in ("$1,250.00"): its number by ranges.js numberRead, the one reader (#874), once the dollar sign,
   thousands commas and spaces are gone; none for an empty field, NaN for a negative or what isn't a number */
const amountIn = v => {
  const t = String(v).replace(/[$,\s]/g, ""), n = t === "" ? null : numberRead(t);
  return t === "" ? null : n && !/[-+e]/i.test(t) ? n.x : NaN;
};
async function costSend(path, method, body, msg, then) {   // a plan year's or a bill's change; its reply's oop put in place
  sayIn(msg, "Saving…");
  try {
    const r = await api(`profiles/${PROFILE.id}/costs/${path}`, {method, body: body ? JSON.stringify(body) : undefined});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save (${r.status}).`, true); return; }
    then();
    if (j.records) storeRecords(j.records); else storePut("oop", j);   // a bill's file is a document new to the page (#1128)
  } catch (e) { sayIn(msg, "Couldn't reach the server: " + e.message, true); }
}
function planYearsIn() {
  const ys = (DATA.oop || {}).years || [];
  if (state.preset === "all") return ys;
  const a = isoDay(state.from), b = isoDay(state.to);
  return ys.filter(y => y.start <= b && y.end >= a);
}
const planMeter = (label, got, of, note) => el("div", {class: "ov-row still"}, el("span", {class: "n", text: label}),
  el("span", {class: "v", text: `${usd(got)} of ${usd(of)}`}),
  el("span", {class: "s cost-bars"}, el("span", {class: "cost-bar-run", "aria-hidden": "true"},
    el("span", {class: "cost-bar", style: {width: (of > 0 ? Math.min(1, Math.max(0, got / of)) * 100 : got > 0 ? 100 : 0) + "%", background: "var(--measure)"}})),
    note ? el("span", {class: "cost-bar-note", text: note}) : null));
function planForm(p) {   // a plan year typed: new (p null) or changed
  const msg = el("p", {class: "hint", id: "planMsg"}), id = f => "plan-" + f;
  const f = (k, label, v, type = "text") => el("label", {for: id(k)}, el("span", {text: label}),
    el("input", {id: id(k), type, inputmode: type === "text" ? "decimal" : null, autocomplete: "off", value: v == null ? "" : String(v), size: type === "text" ? 9 : null}));
  const form = el("div", {class: "oop-form needs-server"}, f("start", "Plan year starts", p?.start, "date"), f("deductible", "Deductible", p?.deductible),
    f("oop", "Out-of-pocket max", p?.oop), f("familyDeductible", "Family deductible (optional)", p?.familyDeductible),
    f("familyOop", "Family out-of-pocket max (optional)", p?.familyOop));
  const back = () => { planState.edit = null; keepFocus(renderCosts); $(p ? `#plan-${p.id} .plan-change` : "#planAdd")?.focus(); };
  form.append(el("button", {class: "btn primary", type: "button", text: "Save", onclick: () => {
    const body = {start: $("#plan-start").value};
    for (const k of ["deductible", "oop", "familyDeductible", "familyOop"]) {
      body[k] = amountIn($("#plan-" + k).value);
      if (Number.isNaN(body[k])) { sayIn(msg, `Type the ${$(`label[for=plan-${k}] span`).textContent.replace(/ \(optional\)/, "").toLowerCase()} as an amount, such as 3000.`, true); $("#plan-" + k).focus(); return; }
    }
    costSend(p ? `plans/${p.id}` : "plans", p ? "PUT" : "POST", body, msg, () => { planState.edit = null;
      requestAnimationFrame(() => { ($(p ? `#plan-${p.id} .plan-change` : "#planAdd") || $("#planCard h2")).focus(); announce("Plan year saved."); }); });
  }}), el("button", {class: "btn", type: "button", text: "Cancel", onclick: back}));
  return el("div", {class: "oop-edit", id: "planEdit"}, el("h3", {class: "sub", text: p ? "Change the plan year" : "Add a plan year"}), form, msg);
}
function planCard() {
  const o = DATA.oop || {}, plans = o.plans || [], ys = planYearsIn(), byId_ = new Map(plans.map(p => [p.id, p]));
  const claimOf = new Map((DATA.costs || []).map(c => [c.id, c]));
  const card = el("div", {class: "card", id: "planCard"}, el("div", {class: "list-hd"}, el("h2", {tabindex: -1, text: "Deductible and out-of-pocket max"}), el("span", {class: "spacer"}),
    canEdit() ? el("button", {class: "btn needs-server", type: "button", id: "planAdd", "aria-expanded": planState.edit === "new", text: "Add a plan year",
      onclick: () => { planState.edit = planState.edit === "new" ? null : "new"; keepFocus(renderCosts); $("#plan-start")?.focus(); }}) : null),
    el("p", {class: "hint", text: plans.length ? "Your share on each claim in the plan year, and the part of it toward the deductible where the claim says."
      : "Your share on each claim, by calendar year. Add a plan year to see it against your plan's deductible and out-of-pocket max."}));
  if (planState.edit === "new") card.append(planForm(null));
  if (!ys.length) card.append(el("p", {class: "hint", text: "No claim in this period lists a share for you to pay, and no plan year is typed in for it."}));
  // the newest three, the rest under Earlier years (a long "All" stays short), open where a plan year being changed is
  const later = ys.length > 3 ? el("details", {class: "more mt-3", open: ys.slice(3).some(y => y.plan != null && y.plan === planState.edit) || null},
    el("summary", {text: `${ys.length - 3} earlier year${ys.length === 4 ? "" : "s"}`})) : null;
  ys.forEach((y, n) => {
    const p = y.plan != null ? byId_.get(y.plan) : null, part = el("div", {id: p ? "plan-" + p.id : null, class: "mt-3"});
    const split = y.noSplit ? `${y.noSplit} of ${y.claims.length} claim${y.claims.length === 1 ? " gives" : "s give"} no deductible part` : "";
    part.append(el("h3", {class: "sub", text: p ? `Plan year ${fmtD(dayTs(y.start))} to ${fmtD(dayTs(y.end))}` : plans.length ? `${y.start.slice(0, 4)}, outside the plan years typed in` : y.start.slice(0, 4)}));
    if (p) {
      part.append(planMeter("Deductible", y.towardDeductible, p.deductible, split), planMeter("Out-of-pocket max", y.outOfPocket, p.oop));
      if (p.familyDeductible != null) part.append(planMeter("Family deductible", y.towardDeductible, p.familyDeductible, "this profile's claims only"));
      if (p.familyOop != null) part.append(planMeter("Family out-of-pocket max", y.outOfPocket, p.familyOop, "this profile's claims only"));
    } else part.append(el("p", {text: `${usd(y.towardDeductible)} toward the deductible · ${usd(y.outOfPocket)} out of pocket`}), split ? el("p", {class: "hint", text: split + "."}) : null);
    const cs = y.claims.map(i => claimOf.get(i)).filter(Boolean);
    if (cs.length) part.append(el("details", {class: "more"}, el("summary", {text: `The ${cs.length} claim${cs.length === 1 ? "" : "s"} counted`}),
      scrollBox({class: "tablewrap", "aria-label": "Claims counted"}, stacked(el("table", {class: "data"},
        el("thead", {}, el("tr", {}, ["Date", "Service", "Provider", "Toward the deductible", "Your share"].map((h, i) => el("th", {class: i > 2 ? "num" : null, text: h})))),
        el("tbody", {}, cs.map(c => el("tr", {}, el("td", {text: fmtD(dayTs(c.d))}), el("td", {text: c.title}), el("td", {text: c.prov || ""}),
          el("td", {class: "num", text: typeof c.ded === "number" ? usd2(c.ded) : "—"}), el("td", {class: "num", text: usd2(c.owe)})))))))));
    if (p && canEdit()) {
      const msg = el("p", {class: "hint"});
      part.append(el("div", {class: "btns my-2"}, el("button", {class: "btn plan-change needs-server", type: "button", "aria-expanded": planState.edit === p.id, text: "Change",
        onclick: () => { planState.edit = planState.edit === p.id ? null : p.id; keepFocus(renderCosts); $("#plan-start")?.focus(); }}),
        el("button", {class: "btn needs-server", type: "button", text: "Remove", onclick: async () => {
          if (await ask({title: `Remove the plan year from ${fmtD(dayTs(p.start))}?`, message: "The claims stay as they are.", action: "Remove", danger: true}))
            costSend(`plans/${p.id}`, "DELETE", null, msg, () => { if (planState.edit === p.id) planState.edit = null; requestAnimationFrame(() => $("#planCard h2").focus()); });
        }})), msg);
      if (planState.edit === p.id) part.append(planForm(p));
    }
    (n < 3 ? card : later).append(part);
  });
  if (later) card.append(later);
  return card;
}
/* ---- bills (#1128): a provider's bill typed in (owners and editors), lined up against the claim's share, worked out
   by provider, date of service and amount or chosen (server: costplan.lined_up, DATA.oop.bills), with the payments
   matched to that claim. Plain facts. Its file, when given, is saved first as a document typed in (an import of its
   own, as Sources' hand entry saves one) and opens in the drawer; never read for numbers */
const billState = {edit: null, first: false};   // the bill being typed: "new", its id, or null; first: Add a bill from the empty page
const billFacts = b => b.claim == null ? (b.none ? "No claim for this bill yet, as chosen." : "No claim found for this bill yet.")
  : Math.abs(b.amount - b.owe) < 0.005 ? "Billed matches what your plan says you owe."
  : `Billed ${usd2(b.amount)}; your plan says you owe ${usd2(b.owe)}, a difference of ${usd2(Math.abs(b.amount - b.owe))}.`;
async function billFile(file, service, provider) {   // the bill's file, saved as a document typed in: its import's id
  const pid = PROFILE.id, call = async (path, opts) => {
    const r = await api(`profiles/${pid}/${path}`, opts), j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `${r.status} ${r.statusText}`);
    return j;
  };
  const up = (await call("uploads", {method: "POST"})).id;
  try {
    await call(`uploads/${up}/files?path=${encodeURIComponent(file.name)}`, {method: "PUT", body: file, headers: {"Content-Type": "application/octet-stream"}});
    await call(`uploads/${up}/finish`, {method: "POST"});
    return (await call("manual", {method: "POST", body: JSON.stringify({save: true, keep_duplicates: true,
      items: [{kind: "document", date: service, title: `Bill: ${provider}`, upload: String(up), file: file.name}]})})).import_id;
  } catch (e) { api(`profiles/${pid}/uploads/${up}`, {method: "DELETE"}).catch(() => {}); throw e; }
}
function billForm(b) {   // a bill typed: new (b null) or changed
  const msg = el("p", {class: "hint", id: "billMsg"}), id = f => "bill-" + f;
  const f = (k, label, v, type = "text", more = {}) => el("label", {for: id(k)}, el("span", {text: label}),
    el("input", {id: id(k), type, autocomplete: "off", value: v == null ? "" : String(v), ...more}));
  const near = (DATA.costs || []).filter(c => c.id != null && byId.get(c.id)).sort((x, y) => Math.abs(dayTs(x.d) - dayTs(b?.service || isoDay(Date.now())))
    - Math.abs(dayTs(y.d) - dayTs(b?.service || isoDay(Date.now())))).slice(0, 40);
  const chosen = b && b.match === "marked" ? String(b.claim) : b && b.none ? "0" : "-1";
  const claim = el("select", {id: id("claim")}, el("option", {value: "-1", text: b && b.match === "auto" ? "Work it out (matched now)" : "Work it out"}),
    el("option", {value: "0", text: "No claim yet", selected: chosen === "0" || null}),
    ...near.map(c => el("option", {value: String(c.id), selected: chosen === String(c.id) || null,
      text: `${fmtD(dayTs(c.d))} · ${c.prov || c.title} · billed ${usd2(c.billed)} · your share ${usd2(c.owe)}`})));
  const form = el("div", {class: "oop-form needs-server"}, f("service", "Date of service", b?.service, "date"), f("provider", "Provider", b?.provider, "text", {size: 24}),
    f("amount", "Amount billed", b?.amount, "text", {inputmode: "decimal", size: 9}), f("due", "Due (optional)", b?.due, "date"),
    el("label", {for: id("claim")}, el("span", {text: "Its claim"}), claim),
    f("file", b?.file != null ? "Replace its file (optional)" : "The bill's file (optional)", null, "file", {accept: ".pdf,.png,.jpg,.jpeg,.gif"}));
  const drop = b?.file != null ? el("label", {class: "toggle"}, el("input", {type: "checkbox", id: id("drop")}), "Remove its file") : null;
  if (drop) form.append(drop);
  const back = () => { billState.edit = null; billState.first = false; keepFocus(renderCosts);   // from the empty page: back to it
    ($(b ? `#bill-${b.id} .bill-change` : "#billAdd") || $("#billFirst"))?.focus(); };
  form.append(el("button", {class: "btn primary", type: "button", text: "Save", onclick: async () => {
    const body = {service: $("#bill-service").value, provider: $("#bill-provider").value.trim(), amount: amountIn($("#bill-amount").value),
      due: $("#bill-due").value || null, claim: +claim.value, drop_file: !!$("#bill-drop")?.checked};
    if (Number.isNaN(body.amount)) { sayIn(msg, "Type the amount billed as an amount, such as 1240.", true); $("#bill-amount").focus(); return; }
    const file = $("#bill-file").files[0];
    if (file) {
      if (!body.service || !body.provider) { sayIn(msg, "Type the date of service and the provider first.", true); return; }
      sayIn(msg, "Saving the bill's file…");
      try { body.file_import = await billFile(file, body.service, body.provider); }
      catch (e) { sayIn(msg, "The bill's file wasn't saved: " + e.message, true); return; }
    }
    costSend(b ? `bills/${b.id}` : "bills", b ? "PUT" : "POST", body, msg, () => { billState.edit = null;
      requestAnimationFrame(() => { ($(b ? `#bill-${b.id} .bill-change` : "#billAdd") || $("#billCard h2")).focus(); announce("Bill saved."); }); });
  }}), el("button", {class: "btn", type: "button", text: "Cancel", onclick: back}));
  return el("div", {class: "oop-edit", id: "billEdit"}, el("h3", {class: "sub", text: b ? "Change the bill" : "Add a bill"}), form, msg);
}
function billCard() {
  const all = (DATA.oop || {}).bills || [], list = state.preset === "all" ? all : all.filter(b => b.service >= isoDay(state.from) && b.service <= isoDay(state.to));
  if (!all.length && !canEdit()) return null;
  const card = el("div", {class: "card", id: "billCard"}, el("div", {class: "list-hd"}, el("h2", {tabindex: -1, text: "Bills"}), el("span", {class: "spacer"}),
    canEdit() ? el("button", {class: "btn needs-server", type: "button", id: "billAdd", "aria-expanded": billState.edit === "new", text: "Add a bill",
      onclick: () => { billState.edit = billState.edit === "new" ? null : "new"; keepFocus(renderCosts); $("#bill-service")?.focus(); }}) : null),
    el("p", {class: "hint", text: "A provider's bill beside what your plan says you owe for it."}),
    aboutWords("Each bill is lined up with the claim of the same provider and date of service, and what that claim says you owe."));
  if (billState.edit === "new") card.append(billForm(null));
  if (!list.length) card.append(emptyState(all.length ? "No bill in this period." : "No bills typed in yet.", null, {cls: ""}));   // (#494)
  for (const b of list) {
    const c = b.claim != null ? byId.get(b.claim) : null, msg = el("p", {class: "hint"});
    const lines = [billFacts(b)];
    if (b.claim != null) lines.push(`Billed ${usd2(b.amount)} · owed per plan ${usd2(b.owe)} · paid so far ${usd2(b.paid)}${b.payments.length ? ` (${b.payments.length} payment${b.payments.length === 1 ? "" : "s"})` : ""}`,
      `Claim of ${c ? fmtD(dayTs(c.t)) : "—"}${c ? `: ${c.title}` : ""}, ${b.match === "auto" ? "matched by provider, date and amount" : "chosen by hand"}.`);
    if (b.due) lines.push(`Due ${fmtD(dayTs(b.due))}.`);
    const part = el("div", {id: "bill-" + b.id, class: "mt-3 bill-lines"}, el("h3", {class: "sub", text: `${b.provider}, ${fmtD(dayTs(b.service))} · ${usd2(b.amount)}`}),
      ...lines.map((l, i) => el("p", {class: i ? "hint" : null, text: l})));
    const btns = el("div", {class: "btns my-2"});
    if (b.file != null && byId.get(b.file)) btns.append(el("button", {class: "btn", type: "button", text: "Open the bill", onclick: () => openEvent(byId.get(b.file))}));
    if (c) btns.append(el("button", {class: "btn", type: "button", text: "Open claim", onclick: () => openEvent(c)}));
    if (canEdit()) btns.append(el("button", {class: "btn bill-change needs-server", type: "button", "aria-expanded": billState.edit === b.id, text: "Change",
      onclick: () => { billState.edit = billState.edit === b.id ? null : b.id; keepFocus(renderCosts); $("#bill-service")?.focus(); }}),
      el("button", {class: "btn needs-server", type: "button", text: "Remove", onclick: async () => {
        if (await ask({title: `Remove the bill from ${b.provider}?`, message: "Its file, if it has one, stays among the records' documents.", action: "Remove", danger: true}))
          costSend(`bills/${b.id}`, "DELETE", null, msg, () => { if (billState.edit === b.id) billState.edit = null; requestAnimationFrame(() => $("#billCard h2").focus()); });
      }}));
    if (btns.childNodes.length) part.append(btns);
    part.append(msg);
    if (billState.edit === b.id) part.append(billForm(b));
    card.append(part);
  }
  return card;
}
/* ---- the tax-year report (#1136): a tax year's payments as one zip, a PDF summary, a CSV and the receipts
   (server/taxreport.py). The year is the calendar year, or from the month the profile's setting costs.taxStart says
   (owners and editors change it here); its preview (total, how many, how many without a receipt) is the server's,
   so it's exactly what the zip holds. Facts only, with a word to check what counts with a tax adviser */
const taxState = {year: null, seen: {}};   // the year picked; previews by "year/month"
const taxStart = () => Math.min(12, Math.max(1, Math.round(+(CFG.costs || {}).taxStart || 1)));
const taxYearOf = (d, m) => +d.slice(0, 4) - (+d.slice(5, 7) < m ? 1 : 0);
const taxLabel = (y, m) => m === 1 ? String(y) : `${y}–${String(y + 1).slice(2)}`;
const MONTH_NAMES = Array.from({length: 12}, (_, i) => new Date(2026, i, 15).toLocaleDateString("en-US", {month: "long"}));
async function taxPreview(y, m) {
  const k = `${y}/${m}`, out = $("#taxSays");
  if (!taxState.seen[k]) {
    try {
      const r = await api(`profiles/${PROFILE.id}/costs/tax-report?year=${y}&preview=1`), j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `${r.status}`);
      taxState.seen[k] = j;
    } catch (e) { if (out) sayIn(out, "Couldn't get the year's figures: " + e.message, true); return; }
  }
  const j = taxState.seen[k];
  if (!out || taxState.year !== y || taxStart() !== m) return;   // another picked since
  out.textContent = `${fmtD(dayTs(j.from))} to ${fmtD(dayTs(j.to))}: ${usd2(j.total)} in ${j.count} payment${j.count === 1 ? "" : "s"}`
    + ` · ${j.receipts} with a receipt · ${j.missing} without one.`;
}
function taxCard() {
  const m = taxStart(), ys = [...new Set(oopRows().filter(r => r.type === "pay" && r.dup == null && r.d).map(r => taxYearOf(r.d, m)))].sort((a, b) => b - a);
  if (!ys.length) return null;
  if (!ys.includes(taxState.year)) taxState.year = ys[0];
  const y = taxState.year;
  const year = el("select", {id: "taxYear", onchange: e => { taxState.year = +e.target.value; keepFocus(renderCosts); }},
    ...ys.map(v => el("option", {value: String(v), selected: v === y || null, text: taxLabel(v, m)})));
  const month = canEdit() ? el("select", {id: "taxMonth", class: "needs-server", onchange: async e => {
    const v = +e.target.value, msg = $("#taxSays");
    try {
      const r = await api(`profiles/${PROFILE.id}/settings/costs.taxStart`, {method: "PUT", body: JSON.stringify({value: v})}), j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `${r.status}`);
      CFG.costs = {...CFG.costs, taxStart: v}; taxState.year = null; keepFocus(renderCosts); $("#taxMonth")?.focus(); announce("Tax year start saved.");
    } catch (x) { sayIn(msg, "Couldn't save it: " + x.message, true); }
  }}, ...MONTH_NAMES.map((n, i) => el("option", {value: String(i + 1), selected: i + 1 === m || null, text: n}))) : null;
  const card = el("div", {class: "card", id: "taxCard"}, el("h2", {tabindex: -1, text: "Tax-year report"}),
    el("p", {class: "hint", text: "One zip of a tax year's payments, to keep or to give your tax adviser."}),
    aboutWords("The zip holds a printable summary, a spreadsheet (CSV) of every payment and the receipts on file, each named by its row.",
      "It says what was paid, when and to whom; what counts for taxes, check with a tax adviser."),
    el("div", {class: "oop-form"}, el("label", {for: "taxYear"}, el("span", {text: "Tax year"}), year),
      month ? el("label", {for: "taxMonth"}, el("span", {text: "Tax year starts in"}), month) : el("p", {class: "hint", text: `The tax year starts in ${MONTH_NAMES[m - 1]}.`}),
      el("a", {class: "btn primary needs-server", id: "taxGet", href: `api/profiles/${PROFILE.id}/costs/tax-report?year=${y}`, download: "", text: "Download the zip"})),
    el("p", {id: "taxSays", class: "my-2", role: "status", text: "Adding up the year…"}));
  requestAnimationFrame(() => taxPreview(y, m));
  return card;
}
/* ---- the cards further down, found from the top (#1127, #1128, #1136): a line of links near the top of Costs, each
   opening its card's view and moving focus to its heading (the command palette's Plan years, Bills and Tax-year
   report do the same: palette.js TAB_VIEWS). Only the cards there are: plan years with claims, bills for someone who
   may add one or with some, the report with payments */
const COST_CARDS = {plan: ["claims", "planCard", "Plan years"], bill: ["paid", "billCard", "Bills"], tax: ["paid", "taxCard", "Tax-year report"]};
function costJump(k) {
  const [view, id] = COST_CARDS[k];
  if (costState.view !== view && (DATA.costs || []).length) { costState.view = view; hideTip(); renderCosts(); save(); tabAddress(true); }
  const h = $(`#${id} h2`);
  if (h) { h.scrollIntoView({block: "center", behavior: smoothly()}); h.focus({preventScroll: true}); }
}
function costLinks(claims) {
  const ks = [claims && "plan", (canEdit() || ((DATA.oop || {}).bills || []).length) && "bill",
    oopRows().some(r => r.type === "pay" && r.dup == null) && "tax"].filter(Boolean);
  if (!ks.length) return null;
  return el("nav", {"aria-label": "Further down Costs", class: "mb-2"}, el("p", {class: "hint m-0"}, "Further down: ",
    ...ks.flatMap((k, i) => [i ? " · " : null, el("a", {href: "#" + COST_CARDS[k][1], text: COST_CARDS[k][2], onclick: e => { e.preventDefault(); costJump(k); }})]).filter(Boolean)));
}
/* ---- the charts (#300), on the chart kit (chartkit.js): its scales, value grid, time axis, crosshair and pinned
   readout; their gutters, rows and offsets grow with larger text (ckK, #633). Money on a value axis: "$30K", "$2.5K",
   never rounded to another gridline's (#654) */
const usdAxis = v => v >= 1e6 ? `$${ckNum(v / 1e6)}M` : v >= 1e3 ? `$${ckNum(v / 1e3)}K` : `$${ckNum(v)}`;
/* the value gutter: as wide as the widest of these numbers */
const costGutter = vals => Math.max(...vals.map(v => labelW(usdAxis(v), ""))) + 12;
function drawPaidCum(svg, claimRows, payRows) {
  const pts = [...claimRows.filter(r => r.owe).map(r => ({d: r.d, a: r.owe, s: 0})), ...payRows.map(r => ({d: r.d, a: r.amount, s: 1}))].sort((a, b) => a.d.localeCompare(b.d));
  if (!pts.length) { svg.replaceChildren(); svg.removeAttribute("tabindex"); return; }   // nothing to step through: no tab stop (#652)
  const t0 = dayTs(pts[0].d), t1 = Math.max(dayTs(pts.at(-1).d), t0 + 30 * DAY);
  const series = [[], []], acc = [0, 0];
  for (const p of pts) { acc[p.s] += p.a; series[p.s].push([dayTs(p.d), acc[p.s]]); }
  // money is a measure, not a kind of record (#301): your share in --measure, what was paid in ink
  const color = ["var(--measure)", INK2], label = ["Your share", "Bills paid"], ends = label.map((l, i) => `${l} ${usd(acc[i])}`);
  const max = Math.max(1, acc[0], acc[1]) * 1.08, vals = niceTicks(0, max, 4), k = ckK();
  const w = Math.max(240, svg.parentElement.clientWidth - 32), endW = Math.max(...ends.map(t => labelW(t, "cum-end"))) + 25;
  const narrow = w < 560 || w - endW < 360, L = costGutter(vals), R = w - (narrow ? 10 : endW + 8);   // 240: fits 320 px (#202)
  const T = narrow ? 34 * k : 10 * k, B = T + (narrow ? 140 : 164), h = B + ckAxisH(2);   // 210 at the usual size, as made (#473)
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg._geom = {L, R, T, B, w};
  const x = ckScale(t0, t1, L, R), y = ckScale(0, max, B, T), g = [];
  ckGrid(g, y, vals, L, R, usdAxis);
  ckAxis(g, ckTimeTicks(t0, t1, x, w), L, R, B, B + 16);
  series.forEach((s, i) => {
    if (!s.length) return;
    let dp = `M${x(t0)},${y(0)}`;
    for (const [t, v] of s) dp += `H${x(t)}V${y(v)}`;
    g.push(el("svg:path", {d: dp + `H${x(t1)}`, fill: "none", stroke: color[i], "stroke-width": 2, "stroke-linejoin": "round"}));
    // its end label: the text in ink and a short line key in the series' colour before it, so the colour is on the
    // mark, not the text (#250: orange and green text were under 4.5:1). Narrow, the two stack above the chart.
    const lx = narrow ? 4 : R + 8, ly = narrow ? (11 + i * 14) * k : y(acc[i]) + 4 * k + (i && Math.abs(y(acc[0]) - y(acc[1])) < 14 * k ? 14 * k : 0);
    g.push(el("svg:line", {class: "cum-key", x1: lx, x2: lx + 12, y1: ly - 4 * k, y2: ly - 4 * k, stroke: color[i], "stroke-width": 2.5, "stroke-linecap": "round"}),
      el("svg:text", {class: "cum-end", x: lx + 17, y: ly, text: ends[i]}));
  });
  // the crosshair and the readout pinned over it (ckTip), by pointer, or stepped through each change from the keyboard
  const at = (s, t) => { let v = 0; for (const [tt, vv] of s) { if (tt <= t) v = vv; else break; } return v; };
  const cross = ckCross(T, B), hit = el("svg:rect", {x: L, y: T, width: R - L, height: B - T, fill: "transparent"});
  const show = t => {
    if (t == null) { ckCrossAt(cross, null); return hideTip(); }
    t = Math.max(t0, Math.min(t1, t)); ckCrossAt(cross, x(t));
    const a = at(series[0], t), b = at(series[1], t), rows = [el("div", {class: "v", text: fmtD(t)}),
      el("div", {class: "s"}, el("span", {class: "k", style: {background: color[0]}}), `Your share so far ${usd(a)}`),
      el("div", {class: "s"}, el("span", {class: "k", style: {background: color[1]}}), `Bills paid so far ${usd(b)}`),
      el("div", {class: "s", text: `Difference ${usd(a - b)}`})];
    ckTip(svg, x(t), rows);
    return tipWords(rows);
  };
  hit.addEventListener("pointermove", ev => { const r = svg.getBoundingClientRect(); show(x.inv((ev.clientX - r.left) * (w / r.width))); });
  hit.addEventListener("pointerleave", () => show(null));
  g.push(cross, hit);
  svg.replaceChildren(...g);
  const days = [...new Set(series.flat().map(p => p[0]))].sort((p, q) => p - q);
  chartKeys(svg, `Your share and the bills you paid, running totals: ${days.length} dates from ${fmtD(days[0])} to ${fmtD(days.at(-1))}. Left and right arrow keys step through them. The table lists them by month.`,
    () => days, show, () => show(null));
}
function groupCosts(rows, keyFn) {
  const m = new Map();
  for (const r of rows) {
    const k = keyFn(r); const g = m.get(k) || {key: k, n: 0, billed: 0, paid: 0, owe: 0, first: r.d, last: r.d};
    g.n++; g.billed += r.billed || 0; g.paid += r.paid || 0; g.owe += r.owe || 0;
    if (r.d < g.first) g.first = r.d; if (r.d > g.last) g.last = r.d; m.set(k, g);
  }
  return [...m.values()].sort((a, b) => (b.paid + b.owe) - (a.paid + a.owe));
}
/* a type of care, a provider or a drug picked: the claims drawn again and scrolled to, focus on their heading, in
   sight, not left on the row above the screen (#462, as Body's tiles do, #455) */
function toClaims() {
  keepFocus(renderCosts);
  const t = $("#costTable"); t.scrollIntoView({behavior: smoothly()}); t.querySelector("h2").focus({preventScroll: true});
}
function barList(card, groups, onPick, active) {
  const max = Math.max(1, ...groups.map(g => g.paid + g.owe));
  for (const g of groups) {
    const w1 = g.paid / max * 100, w2 = g.owe / max * 100;
    card.append(el("div", {class: "ov-row", ...pressable(() => onPick(g.key)),   // picked, as What you paid's (#456)
      "aria-pressed": active === undefined ? null : active === g.key,   // a type of care picked filters the claims (#202)
      "data-tip": `${g.key}: ${g.n} claims · billed ${usd(g.billed)} · insurance paid ${usd(g.paid)} · your share ${usd(g.owe)}`},
      el("span", {class: "n", text: g.key}),
      el("span", {class: "v", text: usd(g.paid + g.owe)}),
      // the bars across the row, on one scale for every row, and the note whole under them (#481: beside them, the
      // largest's bar squeezed its note to two lines, and bars sized to what each note left didn't compare)
      el("span", {class: "s cost-bars"},
        el("span", {class: "cost-bar-run"},
          el("span", {class: "cost-bar", style: {width: w1 + "%", background: MUTED, borderRadius: w2 > 0.3 ? "2px 0 0 2px" : null}}),
          w2 > 0.3 ? el("span", {class: "cost-bar", style: {width: w2 + "%", background: "var(--measure)", borderRadius: "0 2px 2px 0"}}) : null),
        el("span", {class: "cost-bar-note", text: `${g.n} claim${g.n === 1 ? "" : "s"}${g.owe >= 1 ? ` · your share ${usd(g.owe)}` : ""}`}))));
  }
}
/* the claims' totals by month ("2025-03") or year ("2025"), every one from the first to the last, in order: the
   chart's bars and its table's rows (#60) */
function costBuckets(rows, by = "month") {
  const months = new Map(), yearly = by === "year", cut = yearly ? 4 : 7;
  if (!rows.length) return months;
  const first = rows.reduce((a, r) => r.d < a ? r.d : a, rows[0].d), last = rows.reduce((a, r) => r.d > a ? r.d : a, rows[0].d);
  if (yearly) for (let y = +first.slice(0, 4); y <= +last.slice(0, 4); y++) months.set(String(y), {paid: 0, owe: 0, billed: 0, n: 0});
  else for (let d = zdate(dayTs(first.slice(0, 7) + "-01")); d <= dayTs(last.slice(0, 7) + "-01"); d.setMonth(d.getMonth() + 1)) months.set(d.iso().slice(0, 7), {paid: 0, owe: 0, billed: 0, n: 0});
  for (const r of rows) { const m = months.get(r.d.slice(0, cut)); if (m) { m.paid += r.paid || 0; m.owe += r.owe || 0; m.billed += r.billed || 0; m.n++; } }
  return months;
}
/* the claims by month or year (by), every one from the first shown to the last, as two plots over one time axis (#300):
   your share on a scale of its own, in --measure, so a month's share reads even beside insurance's thousands; under it
   what insurance paid, grey, for context. A hospital stay's months shaded in both; a month's figures by pointer or the
   arrow keys, its slot marked in both */
function drawCostBars(svg, rows, by = "month") {
  if (!rows.length) { svg.replaceChildren(); svg.removeAttribute("tabindex"); return; }   // nothing to step through: no tab stop (#652)
  const months = costBuckets(rows, by), yearly = by === "year", keys = [...months.keys()], k = ckK();
  const start = key => dayTs(key + (yearly ? "-01-01" : "-01")), ts = keys.map(start);
  { const d = zdate(ts.at(-1)); if (yearly) d.setFullYear(d.getFullYear() + 1); else d.setMonth(d.getMonth() + 1); ts.push(d.getTime()); }
  const val = (i, f) => months.get(keys[i])[f], top = f => Math.max(1, ...keys.map((_, i) => val(i, f))) * 1.08;
  const vO = niceTicks(0, top("owe"), 4), vP = niceTicks(0, top("paid"), 2);
  const w = Math.max(240, svg.parentElement.clientWidth - 32), L = costGutter([...vO, ...vP]), R = w - 10;   // 240: fits 320 px (#202)
  const T1 = 24 * k, B1 = T1 + 110, T2 = B1 + 32 * k, B2 = T2 + 56;
  const x = ckScale(ts[0], ts.at(-1), L, R), half = (ts[1] - ts[0]) / 2;
  const tk = ckTimeTicks(ts[0] - DAY, ts.at(-1) - half, t => x(t + half), w, yearly ? 365 : 28), h = B2 + ckAxisH(tk.rows);   // a label under its bar, mid-slot
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h); svg._geom = {L, R, T: T1, B: B2, w};
  const yO = ckScale(0, top("owe"), B1, T1), yP = ckScale(0, top("paid"), B2, T2), g = [];
  keys.forEach((key, i) => {   // hospital stays as shaded slots
    const a = isoDay(ts[i]), b = isoDay(ts[i + 1] - DAY);
    if (STAYS.some(s => isoDay(s.ts) <= b && isoDay(s.te) >= a)) for (const [t, b] of [[T1, B1], [T2, B2]]) g.push(el("svg:rect", {x: x(ts[i]), y: t, width: x(ts[i + 1]) - x(ts[i]), height: b - t, fill: "var(--stay)"}));
  });
  ckUnit(g, "Your share", T1 - 10 * k); ckGrid(g, yO, vO, L, R, usdAxis); ckFoot(g, L, R, B1);
  ckUnit(g, "Paid by insurance", T2 - 10 * k); ckGrid(g, yP, vP, L, R, usdAxis);
  ckAxis(g, tk, L, R, B2, B2 + 16);
  const mid = i => (x(ts[i]) + x(ts[i + 1])) / 2;
  keys.forEach((key, i) => {   // a bar a slot, 24 px at most, rounded at its end (2 px), from its plot's foot
    const bw = Math.max(2, Math.min(24, (x(ts[i + 1]) - x(ts[i])) * .7));
    for (const [f, y, B, fill] of [["owe", yO, B1, "var(--measure)"], ["paid", yP, B2, MUTED]]) {
      const v = val(i, f); if (v > 0) g.push(el("svg:rect", {x: mid(i) - bw / 2, y: Math.min(B - 1, y(v)), width: bw, height: Math.max(1, B - y(v)), rx: Math.min(2, bw / 2), fill}));
    }
  });
  const say = i => yearly ? keys[i] : zdate(ts[i] + 14 * DAY).toLocaleDateString(undefined, {month: "long", year: "numeric"});
  const cross = ckCross(T1, B2), hit = el("svg:rect", {x: L, y: T1, width: R - L, height: B2 - T1, fill: "transparent"});
  const show = t => {
    const i = t == null ? -1 : ts.findLastIndex(s => s <= t);
    if (i < 0 || i >= keys.length) { ckCrossAt(cross, null); return hideTip(); }
    ckCrossAt(cross, mid(i));
    const m = months.get(keys[i]), rows = [el("div", {class: "v", text: say(i)}),
      el("div", {class: "s"}, el("span", {class: "k", style: {background: "var(--measure)"}}), `Your share ${usd(m.owe)}`),
      el("div", {class: "s"}, el("span", {class: "k", style: {background: MUTED}}), `Insurance paid ${usd(m.paid)}`),
      el("div", {class: "s", text: `${m.n} claims · billed ${usd(m.billed)}`})];
    ckTip(svg, mid(i), rows);
    return tipWords(rows);
  };
  hit.addEventListener("pointermove", ev => { const r = svg.getBoundingClientRect(); show(x.inv((ev.clientX - r.left) * (w / r.width))); });
  hit.addEventListener("pointerleave", () => show(null));
  g.push(cross, hit);
  svg.replaceChildren(...g);
  chartKeys(svg, `${yearly ? "Yearly" : "Monthly"} insurance costs: your share, and what insurance paid, for ${keys.length} ${yearly ? "years" : "months"} from ${say(0)} to ${say(keys.length - 1)}. Left and right arrow keys step through them. The table lists them.`,
    () => ts.slice(0, -1), show, () => show(null));
}
