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
function drawCosts() {
  const box = $("#view-costs"); box.replaceChildren();
  const claims = !!(DATA.costs || []).length;
  if (!claims && !((DATA.outOfPocket || {}).payments || []).length) {
    // #199: what would be here, and one way to start (claims also come from a health plan's export or connection)
    box.append(emptyState("Insurance claims and what you paid out of pocket appear here, from a health plan or typed in.",
      canEdit() ? {href: `sources.html?profile=${PROFILE.id}#payment`, text: "Type in a claim or payment"} : null));
    return;
  }
  // a new period starts at the top of the lists, with no payment open
  const seen = [state.preset, state.from, state.to].join();
  if (costState.seen !== seen) { costState.seen = seen; costState.limit = 200; paidState.q = ""; paidState.open = null; paidState.limit = 200; }
  const rows = costRows();
  const periodText = periodLabel(claims ? "all claims on file" : "everything on file");
  if (!claims) { renderPaid(box, rows, periodText); return; }   // payments only: no claims to chart or list
  // Claims | What you paid (#278): the insurance story or the money out of pocket, each with its own figures
  box.append(el("div", {class: "toolbar"},
    // named for what it shows, as Labs' and the Timeline's are; the view in the address (#costs-paid, app.js), Back
    // returning to the one before (#442), and kept for the next visit (save(), core.js; #456)
    el("div", {class: "seg", id: "costView", role: "group", "aria-label": "Show the costs as"}, ...[["claims", "Claims"], ["paid", "What you paid"]].map(([k, l]) =>
      el("button", {type: "button", "aria-pressed": costState.view === k, text: l, onclick: () => { if (costState.view !== k) { costState.view = k; hideTip(); keepFocus(renderCosts); save(); tabAddress(true); } }}))),
    costState.view === "paid" ? null : el("span", {class: "hint", style: {margin: 0}, text: insurerHint(DATA.costs)})));   // where the claims came from
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
    const leg = ckLegend([["var(--measure)", "Your share"], ["var(--muted)", "Paid by insurance"], ["var(--stay)", "Hospital stay", "stay-key"]]);   // a stay's edge too (#450)
    leg.classList.add("mb-2"); mc.append(leg);
    const svg = el("svg:svg", {id: "costChart", role: "img", "aria-label": by === "year" ? "Yearly insurance costs" : "Monthly insurance costs"}); mc.append(svg);
    requestAnimationFrame(() => drawCostBars(svg, rows, by));
  }
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
  if (list.length > costState.limit) tc.append(el("button", {class: "btn", type: "button", style: {marginTop: "8px"}, text: `Show ${Math.min(200, list.length - costState.limit)} more`,
    onclick: () => { const n = costState.limit; costState.limit += 200; renderCosts(); $(`#costTable tbody tr:nth-child(${n + 1}) .cell-btn`)?.focus(); }}));   // the first one added (#443)
  box.append(tc);
  claimsFit(tc.querySelector(".tablewrap"));   // stacked now, not a frame late (#467)
}
/* ---- out of pocket (#55): payments typed in or from a finance app's export, and claims' shares with no payment.
   Where each amount came from, the HSA/FSA tags and the payment-to-claim matches are worked out by the server
   (server/oop.py, DATA.oop); this counts each once: a payment that arrived twice once, a claim's share only until a
   payment is matched to it. */
const paidState = {q: "", open: null, limit: 200};   // limit: the amounts listed, 200 more at a time like the claims (#201)
const OOP_SRC = {typed: "Typed in", export: "Finance export"};
const usd2 = v => (v || 0).toLocaleString("en-US", {style: "currency", currency: "USD"});   // with its cents, as usd (#543)
function oopRows() {
  const pays = (DATA.outOfPocket || {}).payments || [], o = DATA.oop || {};
  const info = new Map((o.payments || []).map(x => [x.i, x]));
  const claimOf = new Map((DATA.costs || []).filter(c => c.id != null).map(c => [c.id, c]));
  const rows = [];
  pays.forEach((p, i) => {
    const x = info.get(i) || {i, key: p.key, src: /^manual:/.test(p.key || "") ? "typed" : "export", hsa: p.hsa || null};
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
const srcText = r => r.type === "claim" ? `Claim: your share${r.ins ? " per " + r.ins : ""}` : OOP_SRC[r.src] + (r.est ? " (estimated)" : "");
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
    lines.push(`Where the number came from: ${srcText(r).toLowerCase()}${r.account ? ", paid from " + r.account : ""}${r.memo ? " (" + r.memo + ")" : ""}.`);
    if (r.dup != null) {
      const o = oopRows().find(x => x.type === "pay" && x.i === r.dup);
      lines.push(`Counted once: it looks like the ${o ? srcText(o).toLowerCase() : "other"} payment${o ? " of " + fmtD(dayTs(o.d)) : ""}, which is counted instead.`);
    }
    if (r.claim) lines.push(`Paid the claim “${r.claim.title}” of ${fmtD(dayTs(r.claim.d))} (your share ${usd2(r.claim.owe)}), ${r.match === "auto" ? "matched by amount, date and provider" : "matched by hand"}; that share isn't counted again.`);
    else if (r.counted) lines.push("Not matched to a claim.");
    if (r.hsa) lines.push(`Tagged ${r.hsa}${r.hsaFrom === "account" ? " from the account it was paid from" : r.hsaFrom === "typed" ? " when it was typed in" : ""}.`);
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
      : [["auto", r.hsaFrom === "account" || r.hsaFrom === "typed" ? `As ${r.hsaFrom === "typed" ? "typed in" : "the account says"} (${r.hsa})` : "Not tagged"], ["HSA", "HSA"], ["FSA", "FSA"], ["none", "Neither"]];
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
  const meta = DATA.outOfPocket || {}, all = oopRows(), claims = !!(DATA.costs || []).length;
  const card = el("div", {class: "card", id: "paidCard"});
  // the view's heading and its ⓘ, named for what it's about, not the period (#447)
  const h2 = el("div", {class: "set-hd"}, el("h2", {text: `Out of pocket · ${periodLabel}`}));
  if (!all.length) {
    card.append(h2, el("p", {class: "hint"}, "No out-of-pocket payments yet, and no claim lists a share for you to pay. ",
      canEdit() ? el("a", {href: `sources.html?profile=${PROFILE.id}#payment`, text: "Type in a payment"}) : null));
    box.append(card); return;
  }
  const rows = paidRows(), counted = rows.filter(r => r.counted);
  const sum = xs => xs.reduce((a, r) => a + r.counted, 0);
  const pays = counted.filter(r => r.type === "pay"), shares = counted.filter(r => r.type === "claim");
  const has = s => all.some(r => r.src === s);
  // each source: its name, what the ⓘ adds, and its shorter name where all three are named (one line at 390 px, #467)
  const ways = [has("typed") && ["payments typed in", ""], has("export") && ["a finance export", meta.exported ? ` (exported ${fmtD(dayTs(meta.exported))})` : "", "an export"],
    has("claim") && ["claims' shares", " with no payment matched", "claims"]].filter(Boolean);
  const from = whole => "From " + (xs => xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs.at(-1))(ways.map(w => whole ? w[0] + w[1] : ways.length > 2 && w[2] || w[0])) + ".";
  // the view's heading, its figures under it in their card (#448); its hint one line, where the amounts came from, and
  // the rest behind its ⓘ: when the export was, how each amount is counted and what isn't (#280, #465)
  h2.append(aboutWords({title: "what you paid"}, from(true), "A payment that arrived two ways is counted once, and a claim's share only until a payment is matched to it. Click a row below to see where its number came from.",
    meta.excluded ? `Not counted: ${meta.excluded}.` : null));
  const top = [h2, el("p", {class: "hint", text: from(false)})];
  const tile = statTile;
  const typed = sum(pays.filter(r => r.src === "typed")), exp = sum(pays.filter(r => r.src === "export"));
  const prem = sum(counted.filter(r => /premium/i.test(r.group)));
  const hsa = sum(counted.filter(r => r.hsa === "HSA")), fsa = sum(counted.filter(r => r.hsa === "FSA"));
  const dups = rows.filter(r => r.dup != null).length, matched = pays.filter(r => r.claim).length;
  // the figures (#278): the total and its two parts first (with no claims, the HSA's), the rest under More figures
  const hsaTile = tile("Paid with an HSA", usd(hsa), "tagged HSA", "oopHsa");
  const head = [tile("Total out of pocket", usd(sum(counted)), `${pays.length} payment${pays.length === 1 ? "" : "s"}${shares.length ? ` + ${shares.length} claim share${shares.length === 1 ? "" : "s"}` : ""}`, "oopTotal"),
    tile("Payments", usd(sum(pays)), [typed && `${usd(typed)} typed in`, exp && `${usd(exp)} from an export`].filter(Boolean).join("\u00a0· ") || "none in this period"),
    claims ? tile("Claim shares with no payment", usd(sum(shares)), "per the insurers; counted until a payment matches") : hsaTile];
  const tiles = [];
  if (claims) tiles.push(tile("Payments matched to claims", matched.toLocaleString("en-US"), dups ? `${dups} that arrived twice, counted once` : "by amount, date and provider, or by hand"), hsaTile);
  else if (dups) tiles.push(tile("Counted once", dups.toLocaleString("en-US"), "payments that arrived twice"));
  tiles.push(tile("Paid with an FSA", usd(fsa), "tagged FSA", "oopFsa"));
  if (prem) tiles.push(tile("Insurance premiums", usd(prem), counted.some(r => r.est && /premium/i.test(r.group)) ? "includes estimated months" : "paid from your accounts"));
  const bal = meta.balance || {}, settled = bal.owed === 0 && bal.asOf ? fmtD(dayTs(bal.asOf)) : "";
  if (claims && has("export")) {   // the finance export's view: what the insurers listed against the bills paid
    const gap = sumOf(claimRows, "owe") - sum(pays.filter(r => r.group === "Medical bills"));
    tiles.push(tile(gap >= 0 ? (settled ? "Reduced or forgiven" : "Share not yet paid") : "Paid beyond listed share", usd(Math.abs(gap)),
      gap >= 0 ? (settled ? `negotiated down or written off\u00a0· you owe $0 as of ${settled}` : "still owed, on a plan, or reduced") : "includes bills from earlier periods"));
  }
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
      cum.append(stacked(chartTable(["Month", "Your share listed", "Bills paid", "Share so far", "Paid so far", settled ? "Reduced so far" : "Difference"], out.reverse())));
    } else {
      const svg = el("svg:svg", {id: "paidChart", role: "img", height: 210, "aria-label": "Your share and the bills you paid, running totals. Table lists them by month."});   // its height from the start, so what's under it doesn't move when it's drawn a frame later (#473)
      const leg = ckLegend([["var(--measure)", "Your share per insurers, running total", "line"], ["var(--ink2)", "Medical bills you paid, running total", "line"]]);
      leg.classList.add("mb-2"); cum.append(leg, svg);
      requestAnimationFrame(() => drawPaidCum(svg, claimRows, bills, settled));
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
    for (const g of groups) host.append(el("div", {class: "ov-row", style: {gridTemplateColumns: "1fr auto"},
      ...pressable(() => pick(g.key)), "aria-pressed": paidState.q === g.key},
      el("span", {class: "n", style: {fontWeight: 500}, text: g.key}), el("span", {class: "v", text: usd(g.v)}),
      el("span", {class: "s cost-bars"},   // the bar the row's width on one scale, its note under it, as the claims' (#483, #485)
        el("span", {class: "cost-bar-run"}, el("span", {class: "cost-bar", style: {width: Math.max(0, g.v / max * 100) + "%", background: "var(--ink2)"}})),
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
  if (list.length > shown) tw.append(el("button", {class: "btn", type: "button", style: {marginTop: "8px"}, text: `Show ${Math.min(200, list.length - shown)} more`,
    onclick: () => { paidState.limit = shown + 200; renderCosts(); document.querySelectorAll("#paidTable tbody tr[data-oop] .cell-btn")[shown]?.focus(); }}));   // the first one added
  if (edit && !edit.parentElement) tw.append(edit);   // its row not listed (a row above picked since): under the list
  card.append(tw);
  box.append(card);
}
/* ---- the charts (#300), on the chart kit (chartkit.js): its scales, value grid, time axis, crosshair and pinned
   readout; their gutters, rows and offsets grow with larger text (ckK, #633). Money on a value axis: "$30K", "$2.5K",
   never rounded to another gridline's (#654) */
const usdAxis = v => v >= 1e6 ? `$${ckNum(v / 1e6)}M` : v >= 1e3 ? `$${ckNum(v / 1e3)}K` : `$${ckNum(v)}`;
/* the value gutter: as wide as the widest of these numbers */
const costGutter = vals => Math.max(...vals.map(v => labelW(usdAxis(v), ""))) + 12;
function drawPaidCum(svg, claimRows, payRows, settled) {
  const pts = [...claimRows.filter(r => r.owe).map(r => ({d: r.d, a: r.owe, s: 0})), ...payRows.map(r => ({d: r.d, a: r.amount, s: 1}))].sort((a, b) => a.d.localeCompare(b.d));
  if (!pts.length) { svg.replaceChildren(); return; }
  const t0 = dayTs(pts[0].d), t1 = Math.max(dayTs(pts.at(-1).d), t0 + 30 * DAY);
  const series = [[], []], acc = [0, 0];
  for (const p of pts) { acc[p.s] += p.a; series[p.s].push([dayTs(p.d), acc[p.s]]); }
  // money is a measure, not a kind of record (#301): your share in --measure, what was paid in ink
  const color = ["var(--measure)", "var(--ink2)"], label = ["Your share", "Bills paid"], ends = label.map((l, i) => `${l} ${usd(acc[i])}`);
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
      el("div", {class: "s", text: `${settled ? "Reduced so far" : "Difference"} ${usd(a - b)}`})];
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
    card.append(el("div", {class: "ov-row", style: {gridTemplateColumns: "1fr auto"}, ...pressable(() => onPick(g.key)),   // picked, as What you paid's (#456)
      "aria-pressed": active === undefined ? null : active === g.key,   // a type of care picked filters the claims (#202)
      "data-tip": `${g.key}: ${g.n} claims · billed ${usd(g.billed)} · insurance paid ${usd(g.paid)} · your share ${usd(g.owe)}`},
      el("span", {class: "n", style: {fontWeight: 500}, text: g.key}),
      el("span", {class: "v", text: usd(g.paid + g.owe)}),
      // the bars across the row, on one scale for every row, and the note whole under them (#481: beside them, the
      // largest's bar squeezed its note to two lines, and bars sized to what each note left didn't compare)
      el("span", {class: "s cost-bars"},
        el("span", {class: "cost-bar-run"},
          el("span", {class: "cost-bar", style: {width: w1 + "%", background: "var(--muted)", borderRadius: w2 > 0.3 ? "2px 0 0 2px" : null}}),
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
  if (!rows.length) { svg.replaceChildren(); return; }
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
    for (const [f, y, B, fill] of [["owe", yO, B1, "var(--measure)"], ["paid", yP, B2, "var(--muted)"]]) {
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
      el("div", {class: "s"}, el("span", {class: "k", style: {background: "var(--muted)"}}), `Insurance paid ${usd(m.paid)}`),
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
