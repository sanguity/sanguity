/* Sanguity · Costs tab: claims and out-of-pocket payments.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- costs */
const costState = {period: "dx", q: "", cat: "", deniedOnly: false, sort: "d", dir: -1, limit: 200};
function costPeriods() {
  const yrs = [...new Set([...(DATA.costs || []), ...((DATA.outOfPocket || {}).payments || [])].map(r => r.d.slice(0, 4)))].sort().reverse();
  return [...msPeriods(), ...yrs.map(y => [y, y]), ["all", "All"]];
}
function costRows() { return inPeriod(DATA.costs || [], costState.period); }
const isDenied = r => /DENIED/i.test(r.status || "");
const sumOf = (rows, k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
function renderCosts() {
  const box = $("#view-costs"); box.replaceChildren();
  if (!(DATA.costs || []).length) { box.append(el("div", {class: "card empty", text: "No insurance claims imported yet."})); return; }
  const rows = costRows();
  const pm = msOf(costState.period);
  const periodLabel = pm ? `since ${lcLabel(pm.label)} (${fmtD(dayTs(pm.date))})` : costState.period === "all" || isMsKey(costState.period) ? "all claims on file" : costState.period;
  // period switch
  box.append(el("div", {class: "toolbar"},
    el("div", {class: "seg"}, ...costPeriods().map(([k, l]) => el("button", {"aria-pressed": costState.period === k, text: l, onclick: () => { costState.period = k; costState.limit = 200; paidState.q = ""; renderCosts(); }}))),
    el("span", {class: "hint", style: {margin: 0}, text: insurerHint(DATA.costs)})));
  // stat tiles
  const billed = sumOf(rows, "billed"), paid = sumOf(rows, "paid"), owe = sumOf(rows, "owe");
  const denied = rows.filter(isDenied).length;
  const tile = (label, value, sub) => el("div", {class: "stat"}, el("div", {class: "n", text: value}), el("div", {class: "l", text: label}), sub ? el("div", {class: "l", style: {marginTop: "2px"}, text: sub}) : null);
  box.append(el("div", {class: "stats"},
    tile("Billed by providers", usd(billed), "list prices, before insurance"),
    tile("Paid by insurance", usd(paid), billed ? `${Math.round(paid / billed * 100)}% of billed` : ""),
    tile("Your share", usd(owe), "per the insurers, before your payments"),
    tile("Discounts & not covered", usd(Math.max(0, billed - paid - owe)), "network discounts, write-offs, denials"),
    tile("Claims", rows.length.toLocaleString(), `${rows.filter(r => r.kind === "pharmacy").length} pharmacy fills`),
    tile("Denied", denied.toLocaleString(), denied ? `${rows.filter(r => /PROVIDER_DENIED/i.test(r.status)).length} provider-denied` : "")));
  renderPaid(box, rows, periodLabel);
  // monthly chart
  const mc = el("div", {class: "card"}, el("h2", {text: `Insurance claims by month · ${periodLabel}`}),
    el("div", {class: "legend", style: {margin: "4px 0 6px"}},
      el("span", {}, el("i", {style: {background: "var(--c-lab)"}}), "Paid by insurance"),
      el("span", {}, el("i", {style: {background: "var(--c-imaging)"}}), "Your share"),
      el("span", {}, el("i", {style: {background: "var(--stay)"}}), "Hospital stay")));
  const svg = el("svg:svg", {role: "img", "aria-label": "Monthly insurance costs"}); mc.append(svg); box.append(mc);
  requestAnimationFrame(() => drawCostMonths(svg, rows));
  // category + provider breakdowns
  const two = el("div", {class: "ov-cols"});
  const catCard = el("div", {class: "card"}, el("h2", {text: "By type of care"}), el("p", {class: "hint", text: "Grouped from billing codes on each claim. Click a row to list those claims below."}));
  const provCard = el("div", {class: "card"}, el("h2", {text: "Top providers"}), el("p", {class: "hint", text: "Ranked by total cost (insurance paid + your share)."}));
  two.append(catCard, provCard); box.append(two);
  barList(catCard, groupCosts(rows, r => r.cat), k => { costState.cat = costState.cat === k ? "" : k; renderCosts(); $("#costTable").scrollIntoView({behavior: "smooth"}); }, costState.cat);
  barList(provCard, groupCosts(rows, r => r.prov || "(unknown)").slice(0, 10), k => { costState.q = k; renderCosts(); $("#costTable").scrollIntoView({behavior: "smooth"}); });
  // pharmacy
  const rx = rows.filter(r => r.kind === "pharmacy");
  if (rx.length) {
    const g = groupCosts(rx, r => r.title.replace(/\s+\d.*$/, ""));
    const pc = el("div", {class: "card"}, el("h2", {text: `Prescriptions (${rx.length} fills)`}));
    pc.append(el("div", {class: "tablewrap", style: {maxHeight: "360px"}}, el("table", {class: "data"},
      el("thead", {}, el("tr", {}, ["Drug", "Fills", "First", "Last", "Insurance paid", "Your share"].map(h => el("th", {text: h})))),
      el("tbody", {}, g.map(x => el("tr", {class: "link", onclick: () => { costState.q = x.key; costState.cat = "Pharmacy"; renderCosts(); $("#costTable").scrollIntoView({behavior: "smooth"}); }},
        el("td", {text: x.key}), el("td", {text: x.n}), el("td", {text: fmtD(dayTs(x.first))}), el("td", {text: fmtD(dayTs(x.last))}),
        el("td", {text: usd(x.paid)}), el("td", {text: usd(x.owe)})))))));
    box.append(pc);
  }
  // claims table
  const tc = el("div", {class: "card", id: "costTable"});
  const cats = [...new Set(rows.map(r => r.cat))].sort();
  let list = rows.filter(r => (!costState.cat || r.cat === costState.cat) && (!costState.deniedOnly || isDenied(r)) &&
    (!costState.q || `${r.title} ${r.prov} ${r.cat} ${r.ins}`.toLowerCase().includes(costState.q.toLowerCase())));
  const k = costState.sort, dir = costState.dir;
  list.sort((a, b) => (typeof a[k] === "number" ? (a[k] - b[k]) : String(a[k]).localeCompare(String(b[k]))) * dir);
  const q = el("input", {class: "search", type: "search", placeholder: "Search service, provider, drug…", value: costState.q});
  q.oninput = () => { clearTimeout(window._cq); window._cq = setTimeout(() => { costState.q = q.value; renderCosts(); const n = $("#costTable input.search"); n.focus(); n.setSelectionRange(n.value.length, n.value.length); }, 250); };
  const sel = el("select", {class: "search", style: {minWidth: "0"}, onchange: e => { costState.cat = e.target.value; renderCosts(); }}, el("option", {value: "", text: "All types of care"}), ...cats.map(c => el("option", {value: c, text: c, selected: c === costState.cat || null})));
  const den = el("label", {style: {display: "inline-flex", gap: "6px", alignItems: "center", fontSize: "13px", color: "var(--ink2)"}},
    el("input", {type: "checkbox", checked: costState.deniedOnly || null, onchange: e => { costState.deniedOnly = e.target.checked; renderCosts(); }}), "Denied only");
  const totals = `${list.length} claims · billed ${usd(sumOf(list, "billed"))} · insurance paid ${usd(sumOf(list, "paid"))} · your share ${usd(sumOf(list, "owe"))}`;
  tc.append(el("h2", {text: "Claims"}), el("div", {class: "toolbar", style: {marginTop: "8px"}}, q, sel, den,
    (costState.q || costState.cat || costState.deniedOnly) ? el("button", {class: "btn", style: {margin: 0}, text: "Clear filters", onclick: () => { Object.assign(costState, {q: "", cat: "", deniedOnly: false}); renderCosts(); }}) : null),
    el("p", {class: "hint", text: totals}));
  const cols = [["d", "Date"], ["title", "Service / drug"], ["prov", "Provider"], ["cat", "Type"], ["billed", "Billed"], ["paid", "Ins. paid"], ["owe", "Your share"], ["status", "Status"]];
  tc.append(el("div", {class: "tablewrap", style: {maxHeight: "none"}}, el("table", {class: "data"},
    el("thead", {}, el("tr", {}, cols.map(([key, h]) => el("th", {style: {cursor: "pointer", whiteSpace: "nowrap"}, text: h + (costState.sort === key ? (costState.dir > 0 ? " ▲" : " ▼") : ""),
      onclick: () => { costState.dir = costState.sort === key ? -costState.dir : (["d", "billed", "paid", "owe"].includes(key) ? -1 : 1); costState.sort = key; renderCosts(); }})))),
    el("tbody", {}, list.slice(0, costState.limit).map(r => el("tr", {class: "link", onclick: () => openEvent(byId.get(r.id))},
      el("td", {text: r.d}), el("td", {text: r.title.length > 60 ? r.title.slice(0, 59) + "…" : r.title}), el("td", {text: r.prov}), el("td", {text: r.cat}),
      el("td", {text: usd(r.billed)}), el("td", {text: usd(r.paid)}), el("td", {class: r.owe > 0 ? "" : "", text: usd(r.owe)}),
      el("td", {class: isDenied(r) ? "abn" : "", text: (r.status || "").replace(/_/g, " ").toLowerCase()})))))));
  if (list.length > costState.limit) tc.append(el("button", {class: "btn", style: {marginTop: "8px"}, text: `Show ${Math.min(200, list.length - costState.limit)} more`, onclick: () => { costState.limit += 200; renderCosts(); }}));
  box.append(tc);
}
/* ---- actually paid (bank/card payments exported from the finance app) */
const paidState = {q: ""};
function paidRows() {
  return inPeriod((DATA.outOfPocket || {}).payments || [], costState.period);
}
function renderPaid(box, claimRows, periodLabel) {
  const meta = DATA.outOfPocket || {}, all = meta.payments || [];
  const card = el("div", {class: "card", id: "paidCard"});
  card.append(el("h2", {text: `Actually paid · ${periodLabel}`}));
  if (!all.length) { card.append(el("p", {class: "hint", text: "No out-of-pocket payments imported yet."})); box.append(card); return; }
  const rows = paidRows();
  const exp = meta.exported ? fmtD(dayTs(meta.exported)) : "";
  card.append(el("p", {class: "hint", text: `Money that left your bank and card accounts, from your finance records (exported ${exp}). ${meta.excluded ? "Not counted: " + meta.excluded + "." : ""}`}));
  const sumG = g => rows.filter(r => r.group === g).reduce((a, r) => a + r.amount, 0);
  const total = rows.reduce((a, r) => a + r.amount, 0), bills = sumG("Medical bills");
  const prem = sumG("Health insurance premiums") + sumG("Dental insurance premiums");
  const share = sumOf(claimRows, "owe"), gap = share - bills;
  const bal = meta.balance || {}, settled = bal.owed === 0 && bal.asOf ? fmtD(dayTs(bal.asOf)) : "";
  const tile = (label, value, sub) => el("div", {class: "stat"},
    el("div", {class: "n", text: value}), el("div", {class: "l", text: label}), sub ? el("div", {class: "l", style: {marginTop: "2px"}, text: sub}) : null);
  card.append(el("div", {class: "stats", style: {marginTop: "10px"}},
    tile("Total out of pocket", usd(total), `${rows.length} payment${rows.length === 1 ? "" : "s"}`),
    tile("Medical bills paid", usd(bills), "hospitals, clinics, pharmacy"),
    tile("Insurance premiums", usd(prem), rows.some(r => r.est && /premium/i.test(r.group)) ? "includes estimated months" : "paid from your accounts"),
    tile("Your share per insurers", usd(share), "from claims, same period"),
    tile(gap >= 0 ? (settled ? "Reduced or forgiven" : "Share not yet paid") : "Paid beyond listed share", usd(Math.abs(gap)),
      gap >= 0 ? (settled ? `negotiated down or written off · you owe $0 as of ${settled}` : "still owed, on a plan, or reduced") : "includes bills from earlier periods")));
  // cumulative: share listed vs bills paid
  const svg = el("svg:svg", {role: "img", "aria-label": "Cumulative share versus payments"});
  card.append(el("div", {class: "legend", style: {margin: "4px 0 6px"}},
    el("span", {}, el("i", {style: {background: "var(--c-imaging)"}}), "Your share per insurers, running total"),
    el("span", {}, el("i", {style: {background: "var(--c-procedure)"}}), "Medical bills you paid, running total")), svg);
  requestAnimationFrame(() => drawPaidCum(svg, claimRows, rows.filter(r => r.group === "Medical bills"), settled));
  // breakdowns
  const two = el("div", {class: "ov-cols", style: {marginTop: "12px"}});
  const g1 = el("div", {}, el("h3", {class: "sub", text: "What it was for"})), g2 = el("div", {}, el("h3", {class: "sub", text: "Who was paid"}));
  two.append(g1, g2); card.append(two);
  const grp = keyFn => { const m = new Map(); for (const r of rows) { const k = keyFn(r), g = m.get(k) || {key: k, n: 0, v: 0}; g.n++; g.v += r.amount; m.set(k, g); } return [...m.values()].sort((a, b) => b.v - a.v); };
  const pick = k => { paidState.q = paidState.q === k ? "" : k; renderCosts(); $("#paidTable").scrollIntoView({behavior: "smooth", block: "nearest"}); };
  const bars = (host, groups) => {
    const max = Math.max(1, ...groups.map(g => g.v));
    for (const g of groups) host.append(el("div", {class: "ov-row", style: {gridTemplateColumns: "1fr auto", background: paidState.q === g.key ? "var(--hover)" : null}, onclick: () => pick(g.key)},
      el("span", {class: "n", style: {fontWeight: 500}, text: g.key}), el("span", {class: "v", text: usd(g.v)}),
      el("span", {class: "s", style: {display: "flex", alignItems: "center"}},
        el("span", {style: {display: "inline-block", height: "8px", width: Math.max(0, g.v / max * 72) + "%", background: "var(--c-procedure)", flex: "none", borderRadius: "2px"}}),
        el("span", {style: {marginLeft: "8px", color: "var(--muted)"}, text: `${g.n} payment${g.n === 1 ? "" : "s"}`}))));
  };
  bars(g1, grp(r => r.group)); bars(g2, grp(r => r.payee));
  // table
  const list = rows.filter(r => !paidState.q || r.group === paidState.q || r.payee === paidState.q).slice().sort((a, b) => b.d.localeCompare(a.d));
  const tw = el("div", {id: "paidTable", style: {marginTop: "12px"}});
  tw.append(el("div", {class: "toolbar", style: {margin: "0 0 6px"}},
    el("span", {class: "hint", style: {margin: 0}, text: `${list.length} payments · ${usd(list.reduce((a, r) => a + r.amount, 0))}${paidState.q ? " · " + paidState.q : ""}`}),
    paidState.q ? el("button", {class: "btn", style: {margin: 0}, text: "Show all payments", onclick: () => { paidState.q = ""; renderCosts(); }}) : null));
  tw.append(el("div", {class: "tablewrap", style: {maxHeight: "360px"}}, el("table", {class: "data"},
    el("thead", {}, el("tr", {}, ["Date", "Paid to", "For", "From account", "Amount"].map(h => el("th", {text: h})))),
    el("tbody", {}, list.map(r => el("tr", {},
      el("td", {text: r.d}), el("td", {text: r.payee}), el("td", {title: r.memo || null, text: r.group + (r.est ? " (estimated)" : "")}), el("td", {text: r.account || ""}),
      el("td", {style: {fontVariantNumeric: "tabular-nums", color: r.amount < 0 ? "var(--good-text)" : null}, text: r.amount < 0 ? `${usd(-r.amount)} refund` : usd(r.amount)})))))));
  card.append(tw);
  box.append(card);
}
function drawPaidCum(svg, claimRows, payRows, settled) {
  const pts = [...claimRows.filter(r => r.owe).map(r => ({d: r.d, a: r.owe, s: 0})), ...payRows.map(r => ({d: r.d, a: r.amount, s: 1}))].sort((a, b) => a.d.localeCompare(b.d));
  if (!pts.length) { svg.replaceChildren(); return; }
  const day = d => dayTs(d);
  const t0 = day(pts[0].d), t1 = Math.max(day(pts[pts.length - 1].d), t0 + 864e5 * 30);
  const series = [[], []], acc = [0, 0];
  for (const p of pts) { acc[p.s] += p.a; series[p.s].push([day(p.d), acc[p.s]]); }
  const w = Math.max(320, svg.parentElement.clientWidth - 32), narrow = w < 560;
  const h = 210, L = 56, R = w - (narrow ? 10 : 150), T = narrow ? 34 : 10, B = h - 24;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h);
  const max = Math.max(1, acc[0], acc[1]) * 1.08;
  const x = t => L + (t - t0) / (t1 - t0) * (R - L), y = v => B - v / max * (B - T);
  const g = [];
  for (const v of niceTicks(0, max, 4)) { g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: y(v), y2: y(v)})); g.push(el("svg:text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", text: v >= 1e3 ? `$${Math.round(v / 1e3)}K` : `$${v}`})); }
  g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}));
  const months = Math.max(1, Math.round((t1 - t0) / 2.63e9)), step = Math.max(1, Math.ceil(months / Math.max(2, Math.floor((R - L) / 60))));
  for (let d = zdate(t0), i = (d.setDate(1), d.setHours(0, 0, 0, 0), d.setMonth(d.getMonth() + 1), 0); d.getTime() <= t1; d.setMonth(d.getMonth() + 1), i++)
    if (i % step === 0) g.push(el("svg:text", {x: x(d.getTime()), y: h - 6, "text-anchor": "middle", text: d.toLocaleDateString(undefined, {month: "short", year: "2-digit"})}));
  const color = ["var(--c-imaging)", "var(--c-procedure)"], label = ["Your share", "Bills paid"];
  series.forEach((s, i) => {
    if (!s.length) return;
    let dp = `M${x(t0)},${y(0)}`;
    for (const [t, v] of s) dp += `H${x(t)}V${y(v)}`;
    dp += `H${x(t1)}`;
    g.push(el("svg:path", {d: dp, fill: "none", stroke: color[i], "stroke-width": 2, "stroke-linejoin": "round"}));
    g.push(narrow
      ? el("svg:text", {x: L + i * 150, y: 14, style: `fill:${color[i]};font-weight:600`, text: `${label[i]} ${usd(acc[i])}`})
      : el("svg:text", {x: R + 8, y: y(acc[i]) + 4 + (i && Math.abs(y(acc[0]) - y(acc[1])) < 14 ? 14 : 0), style: `fill:${color[i]};font-weight:600`, text: `${label[i]} ${usd(acc[i])}`}));
  });
  const at = (s, t) => { let v = 0; for (const [tt, vv] of s) { if (tt <= t) v = vv; else break; } return v; };
  const hit = el("svg:rect", {x: L, y: T, width: R - L, height: B - T, fill: "transparent"});
  hit.addEventListener("pointermove", ev => {
    const r = svg.getBoundingClientRect(), t = t0 + ((ev.clientX - r.left) * (w / r.width) - L) / (R - L) * (t1 - t0);
    const a = at(series[0], t), b = at(series[1], t);
    showTip([el("div", {class: "v", text: fmtD(t)}),
      el("div", {class: "s"}, el("span", {class: "k", style: {background: color[0]}}), `Your share so far ${usd(a)}`),
      el("div", {class: "s"}, el("span", {class: "k", style: {background: color[1]}}), `Bills paid so far ${usd(b)}`),
      el("div", {class: "s", text: `${settled ? "Reduced so far" : "Difference"} ${usd(a - b)}`})], ev);
  });
  hit.addEventListener("pointerleave", hideTip);
  g.push(hit);
  svg.replaceChildren(...g);
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
function barList(card, groups, onPick, active) {
  const max = Math.max(1, ...groups.map(g => g.paid + g.owe));
  for (const g of groups) {
    const w1 = g.paid / max * 100, w2 = g.owe / max * 100;
    card.append(el("div", {class: "ov-row", style: {gridTemplateColumns: "1fr auto", background: active === g.key ? "var(--hover)" : null}, onclick: () => onPick(g.key),
      title: `${g.key}: ${g.n} claims · billed ${usd(g.billed)} · insurance paid ${usd(g.paid)} · your share ${usd(g.owe)}`},
      el("span", {class: "n", style: {fontWeight: 500}, text: g.key}),
      el("span", {class: "v", text: usd(g.paid + g.owe)}),
      el("span", {class: "s", style: {display: "flex", gap: "2px", alignItems: "center"}},
        el("span", {style: {display: "inline-block", height: "8px", width: w1 + "%", background: "var(--c-lab)", borderRadius: w2 > 0.3 ? "2px 0 0 2px" : "2px"}}),
        w2 > 0.3 ? el("span", {style: {display: "inline-block", height: "8px", width: w2 + "%", background: "var(--c-imaging)", borderRadius: "0 2px 2px 0"}}) : null,
        el("span", {style: {marginLeft: "8px", color: "var(--muted)"}, text: `${g.n} claim${g.n === 1 ? "" : "s"}${g.owe >= 1 ? ` · your share ${usd(g.owe)}` : ""}`}))));
  }
}
function drawCostMonths(svg, rows) {
  if (!rows.length) { svg.replaceChildren(); return; }
  const months = new Map();
  const first = rows.reduce((a, r) => r.d < a ? r.d : a, rows[0].d), last = rows.reduce((a, r) => r.d > a ? r.d : a, rows[0].d);
  for (let d = zdate(dayTs(first.slice(0, 7) + "-01")); d <= dayTs(last.slice(0, 7) + "-01"); d.setMonth(d.getMonth() + 1)) months.set(d.iso().slice(0, 7), {paid: 0, owe: 0, billed: 0, n: 0});
  for (const r of rows) { const m = months.get(r.d.slice(0, 7)); if (m) { m.paid += r.paid || 0; m.owe += r.owe || 0; m.billed += r.billed || 0; m.n++; } }
  const keys = [...months.keys()];
  const w = Math.max(320, svg.parentElement.clientWidth - 32), h = 220, L = 64, R = w - 10, T = 10, B = h - 24;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h);
  const max = Math.max(1, ...[...months.values()].map(m => m.paid + m.owe)) * 1.08;
  const slot = (R - L) / keys.length, bw = Math.max(2, Math.min(24, slot * .7));
  const x = i => L + slot * i + slot / 2, y = v => B - v / max * (B - T);
  const g = [];
  // hospital stays as shaded month slots
  keys.forEach((k, i) => { const a = k + "-01", b = k + "-31"; if (STAYS.some(s => isoDay(s.ts) <= b && isoDay(s.te) >= a)) g.push(el("svg:rect", {x: L + slot * i, y: T, width: slot, height: B - T, fill: "var(--stay)"})); });
  for (const v of niceTicks(0, max, 4)) { g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: y(v), y2: y(v)})); g.push(el("svg:text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", text: v >= 1e6 ? `$${(v / 1e6).toFixed(v % 1e6 ? 1 : 0)}M` : v >= 1e3 ? `$${Math.round(v / 1e3)}K` : `$${v}`})); }
  g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}));
  const every = Math.ceil(keys.length / Math.max(2, Math.floor((R - L) / 56)));
  keys.forEach((k, i) => {
    const m = months.get(k), cx = x(i);
    const yp = y(m.paid), yo = y(m.paid + m.owe);
    if (m.paid > 0) g.push(el("svg:rect", {x: cx - bw / 2, y: yp, width: bw, height: Math.max(0, B - yp), fill: "var(--c-lab)", rx: m.owe > 0 && B - yo > 3 ? 0 : 2}));
    if (m.owe > 0) g.push(el("svg:rect", {x: cx - bw / 2, y: yo, width: bw, height: Math.max(1, yp - yo - (m.paid > 0 ? 1 : 0)), fill: "var(--c-imaging)", rx: 2}));
    if (i % every === 0) g.push(el("svg:text", {x: cx, y: h - 6, "text-anchor": "middle", text: zdate(dayTs(k + "-15")).toLocaleDateString(undefined, {month: "short", year: "2-digit"})}));
    const hit = el("svg:rect", {x: L + slot * i, y: T, width: slot, height: B - T, fill: "transparent", style: "cursor:pointer"});
    hit.addEventListener("pointermove", ev => showTip([el("div", {class: "v", text: usd(m.paid + m.owe)}), el("div", {class: "s", text: zdate(dayTs(k + "-15")).toLocaleDateString(undefined, {month: "long", year: "numeric"})}),
      el("div", {class: "s"}, el("span", {class: "k", style: {background: "var(--c-lab)"}}), `Insurance paid ${usd(m.paid)}`),
      el("div", {class: "s"}, el("span", {class: "k", style: {background: "var(--c-imaging)"}}), `Your share ${usd(m.owe)}`),
      el("div", {class: "s", text: `${m.n} claims · billed ${usd(m.billed)}`})], ev));
    hit.addEventListener("pointerleave", hideTip);
    g.push(hit);
  });
  svg.replaceChildren(...g);
}


