/* Sanguity · Costs tab: claims and out-of-pocket costs (typed in, exported, or a claim's share; #55).
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- costs */
/* the period is the records bar's (#171, core.js; "All" is every claim on file). by: the chart's bars, "month" or
   "year" ("": by year once the claims shown span more than four years) */
const costState = {q: "", cat: "", deniedOnly: false, sort: "d", dir: -1, limit: 200, by: "", seen: null};
function costRows() { return inPeriod(DATA.costs || []); }
const isDenied = r => /DENIED/i.test(r.status || "");
const sumOf = (rows, k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
function renderCosts() {
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
  if (costState.seen !== seen) { costState.seen = seen; costState.limit = 200; paidState.q = ""; paidState.open = null; }
  const rows = costRows();
  const periodText = periodLabel(claims ? "all claims on file" : "everything on file");
  if (claims) box.append(el("div", {class: "toolbar"}, el("span", {class: "hint", style: {margin: 0}, text: insurerHint(DATA.costs)})));
  if (!claims) { renderPaid(box, rows, periodText); return; }   // payments only: no claims to chart or list
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
  renderPaid(box, rows, periodText);
  // the chart, by month or by year
  const ds = rows.map(r => r.d).sort(), span = ds.length ? (+ds.at(-1).slice(0, 4) - +ds[0].slice(0, 4)) * 12 + (+ds.at(-1).slice(5, 7) - +ds[0].slice(5, 7)) + 1 : 0;
  const by = costState.by || (span > 48 ? "year" : "month");
  const mc = el("div", {class: "card"}, el("div", {class: "list-hd"}, el("h2", {text: `Insurance claims by ${by} · ${periodText}`}), el("span", {class: "spacer"}),
      el("div", {class: "seg", id: "costBy", role: "group", "aria-label": "Group the claims"}, ...[["month", "By month"], ["year", "By year"]].map(([k, l]) =>
        el("button", {type: "button", "aria-pressed": by === k, text: l, onclick: () => { costState.by = k; renderCosts(); }}))),
      viewSeg("#costBars", "the claims by " + by, renderCosts)));   // Chart | Table (#60)
  box.append(mc);
  if (state.tables.has("#costBars")) mc.append(chartTable([by === "year" ? "Year" : "Month", "Insurance paid", "Your share", "Billed", "Claims"],
    [...costBuckets(rows, by)].reverse().map(([k, m]) => [by === "year" ? k : zdate(dayTs(k + "-15")).toLocaleDateString(undefined, {month: "long", year: "numeric"}),
      usd(m.paid), usd(m.owe), usd(m.billed), m.n.toLocaleString()])));
  else {
    mc.append(el("div", {class: "legend", style: {margin: "4px 0 6px"}},
      el("span", {}, el("i", {style: {background: "var(--c-lab)"}}), "Paid by insurance"),
      el("span", {}, el("i", {style: {background: "var(--c-imaging)"}}), "Your share"),
      el("span", {}, el("i", {style: {background: "var(--stay)"}}), "Hospital stay")));
    const svg = el("svg:svg", {role: "img", "aria-label": by === "year" ? "Yearly insurance costs" : "Monthly insurance costs"}); mc.append(svg);
    requestAnimationFrame(() => drawCostBars(svg, rows, by));
  }
  // category + provider breakdowns
  const two = el("div", {class: "ov-cols"});
  const catCard = el("div", {class: "card"}, el("h2", {text: "By type of care"}), el("p", {class: "hint", text: "Grouped from billing codes on each claim. Click a row to list those claims below."}));
  const provCard = el("div", {class: "card"}, el("h2", {text: "Top providers"}), el("p", {class: "hint", text: "Ranked by total cost (insurance paid + your share)."}));
  two.append(catCard, provCard); box.append(two);
  barList(catCard, groupCosts(rows, r => r.cat), k => { costState.cat = costState.cat === k ? "" : k; renderCosts(); $("#costTable").scrollIntoView({behavior: smoothly()}); }, costState.cat);
  barList(provCard, groupCosts(rows, r => r.prov || "(unknown)").slice(0, 10), k => { costState.q = k; renderCosts(); $("#costTable").scrollIntoView({behavior: smoothly()}); });
  // pharmacy
  const rx = rows.filter(r => r.kind === "pharmacy");
  if (rx.length) {
    const g = groupCosts(rx, r => r.title.replace(/\s+\d.*$/, ""));
    const pc = el("div", {class: "card"}, el("h2", {text: `Prescriptions (${rx.length} fills)`}));
    pc.append(el("div", {class: "tablewrap", style: {maxHeight: "360px"}}, el("table", {class: "data"},
      el("thead", {}, el("tr", {}, ["Drug", "Fills", "First", "Last", "Insurance paid", "Your share"].map(h => el("th", {text: h})))),
      el("tbody", {}, g.map(x => el("tr", {class: "link", onclick: () => { costState.q = x.key; costState.cat = "Pharmacy"; renderCosts(); $("#costTable").scrollIntoView({behavior: smoothly()}); }},
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
  // with filters on: what they keep, with Reset filters, and the totals for the claims shown (#190, filters.js)
  const on = !!(costState.q || costState.cat || costState.deniedOnly);
  const totals = `${on ? "For the claims shown: billed" : `${list.length} claims · billed`} ${usd(sumOf(list, "billed"))} · insurance paid ${usd(sumOf(list, "paid"))} · your share ${usd(sumOf(list, "owe"))}`;
  tc.append(el("h2", {text: "Claims"}), el("div", {class: "toolbar", style: {marginTop: "8px"}}, q, sel, den),
    filterLine(on ? [`Showing ${list.length.toLocaleString()} of ${rows.length.toLocaleString()} claims`, costState.cat && `type of care: ${costState.cat}`,
      costState.q && `matching “${costState.q.trim()}”`, costState.deniedOnly && "denied only"] : [],
      () => { Object.assign(costState, {q: "", cat: "", deniedOnly: false}); renderCosts(); }, () => $("#costTable input.search")),
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
/* ---- out of pocket (#55): payments typed in or from a finance app's export, and claims' shares with no payment.
   Where each amount came from, the HSA/FSA tags and the payment-to-claim matches are worked out by the server
   (server/oop.py, DATA.oop); this counts each once: a payment that arrived twice once, a claim's share only until a
   payment is matched to it. */
const paidState = {q: "", open: null};
const OOP_SRC = {typed: "Typed in", export: "Finance export"};
const usd2 = v => (v || 0).toLocaleString(undefined, {style: "currency", currency: "USD"});
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
    const d = await api(`profiles/${PROFILE.id}/data`);   // a mark changes only the out-of-pocket part
    if (d.ok) DATA.oop = (await d.json()).oop;
    renderCosts();
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
  const btns = el("div", {class: "btns", style: {margin: "6px 0"}});
  if (r.receipt != null && byId.get(r.receipt)) btns.append(el("button", {class: "btn", type: "button", text: "Open receipt", onclick: () => openEvent(byId.get(r.receipt))}));
  if (r.claim && byId.get(r.claim.id)) btns.append(el("button", {class: "btn", type: "button", text: "Open claim", onclick: () => openEvent(byId.get(r.claim.id))}));
  btns.append(el("button", {class: "btn", type: "button", text: "Close", onclick: () => { paidState.open = null; renderCosts(); }}));
  box.append(btns);
  if (canEdit()) {
    const target = r.type === "claim" ? {event: r.id} : {payment: r.key};
    const now = r.type === "claim" ? (r.hsa || "auto") : r.hsaFrom === "marked" ? (r.hsa || "none") : "auto";
    const opts = r.type === "claim" ? [["auto", "Not tagged"], ["HSA", "HSA"], ["FSA", "FSA"]]
      : [["auto", r.hsaFrom === "account" || r.hsaFrom === "typed" ? `As ${r.hsaFrom === "typed" ? "typed in" : "the account says"} (${r.hsa})` : "Not tagged"], ["HSA", "HSA"], ["FSA", "FSA"], ["none", "Neither"]];
    const hsa = el("select", {id: "oopHsaSel"}, ...opts.map(([v, t]) => el("option", {value: v, text: t, selected: v === now || null})));
    const form = el("div", {class: "oop-form"}, el("label", {for: "oopHsaSel"}, el("span", {text: "Paid with"}), hsa));
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
  card.append(el("h2", {text: `Out of pocket · ${periodLabel}`}));
  if (!all.length) {
    card.append(el("p", {class: "hint"}, "No out-of-pocket payments yet, and no claim lists a share for you to pay. ",
      canEdit() ? el("a", {href: `sources.html?profile=${PROFILE.id}#payment`, text: "Type in a payment"}) : null));
    box.append(card); return;
  }
  const rows = paidRows(), counted = rows.filter(r => r.counted);
  const sum = xs => xs.reduce((a, r) => a + r.counted, 0);
  const pays = counted.filter(r => r.type === "pay"), shares = counted.filter(r => r.type === "claim");
  const has = s => all.some(r => r.src === s);
  const ways = [has("typed") && "payments typed in", has("export") && `a finance export${meta.exported ? " (exported " + fmtD(dayTs(meta.exported)) + ")" : ""}`,
    has("claim") && "claims' shares with no payment matched"].filter(Boolean);
  card.append(el("p", {class: "hint", text: `From ${ways.join(", ")}. Each amount is counted once.${meta.excluded ? " Not counted: " + meta.excluded + "." : ""}`}),
    moreAbout("A payment that arrived two ways is counted once, and a claim's share only until a payment is matched to it. Click a row below to see where its number came from."));
  const tile = (label, value, sub, id) => el("div", {class: "stat", id: id || null},
    el("div", {class: "n", text: value}), el("div", {class: "l", text: label}), sub ? el("div", {class: "l", style: {marginTop: "2px"}, text: sub}) : null);
  const typed = sum(pays.filter(r => r.src === "typed")), exp = sum(pays.filter(r => r.src === "export"));
  const prem = sum(counted.filter(r => /premium/i.test(r.group)));
  const hsa = sum(counted.filter(r => r.hsa === "HSA")), fsa = sum(counted.filter(r => r.hsa === "FSA"));
  const dups = rows.filter(r => r.dup != null).length, matched = pays.filter(r => r.claim).length;
  const tiles = [tile("Total out of pocket", usd(sum(counted)), `${pays.length} payment${pays.length === 1 ? "" : "s"}${shares.length ? ` + ${shares.length} claim share${shares.length === 1 ? "" : "s"}` : ""}`, "oopTotal"),
    tile("Payments", usd(sum(pays)), [typed && `${usd(typed)} typed in`, exp && `${usd(exp)} from an export`].filter(Boolean).join(" · ") || "none in this period")];
  if (claims) tiles.push(tile("Claim shares with no payment", usd(sum(shares)), "per the insurers; counted until a payment matches"),
    tile("Payments matched to claims", matched.toLocaleString(), dups ? `${dups} that arrived twice, counted once` : "by amount, date and provider, or by hand"));
  else if (dups) tiles.push(tile("Counted once", dups.toLocaleString(), "payments that arrived twice"));
  tiles.push(tile("Paid with an HSA", usd(hsa), "tagged HSA", "oopHsa"), tile("Paid with an FSA", usd(fsa), "tagged FSA", "oopFsa"));
  if (prem) tiles.push(tile("Insurance premiums", usd(prem), counted.some(r => r.est && /premium/i.test(r.group)) ? "includes estimated months" : "paid from your accounts"));
  const bal = meta.balance || {}, settled = bal.owed === 0 && bal.asOf ? fmtD(dayTs(bal.asOf)) : "";
  if (claims && has("export")) {   // the finance export's view: what the insurers listed against the bills paid
    const gap = sumOf(claimRows, "owe") - sum(pays.filter(r => r.group === "Medical bills"));
    tiles.push(tile(gap >= 0 ? (settled ? "Reduced or forgiven" : "Share not yet paid") : "Paid beyond listed share", usd(Math.abs(gap)),
      gap >= 0 ? (settled ? `negotiated down or written off · you owe $0 as of ${settled}` : "still owed, on a plan, or reduced") : "includes bills from earlier periods"));
  }
  card.append(el("div", {class: "stats", style: {marginTop: "10px"}}, ...tiles));
  // HSA and FSA by year
  const years = new Map();
  for (const r of counted) if (r.hsa) {
    const y = years.get(r.d.slice(0, 4)) || {HSA: 0, FSA: 0, n: 0, rc: 0};
    y[r.hsa] += r.counted; y.n++; if (r.receipt != null) y.rc++;
    years.set(r.d.slice(0, 4), y);
  }
  if (years.size) card.append(el("div", {id: "oopTax"}, el("h3", {class: "sub", text: "HSA and FSA, by year"}),
    el("div", {class: "tablewrap"}, el("table", {class: "data"},
      el("thead", {}, el("tr", {}, ["Year", "HSA", "FSA", "Amounts", "With a receipt"].map(h => el("th", {text: h})))),
      el("tbody", {}, [...years.entries()].sort((a, b) => b[0].localeCompare(a[0])).map(([y, v]) => el("tr", {},
        el("td", {text: y}), el("td", {text: usd2(v.HSA)}), el("td", {text: usd2(v.FSA)}), el("td", {text: v.n}), el("td", {text: v.rc}))))))));
  // cumulative: share listed vs bills paid
  if (claims) {   // Chart | Table (#60): the table by month
    const bills = pays.filter(r => r.group === "Medical bills");
    card.append(el("div", {class: "list-hd", style: {marginTop: "10px"}}, el("h3", {class: "sub", style: {margin: 0}, text: "Your share and the bills you paid, running totals"}),
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
      card.append(chartTable(["Month", "Your share listed", "Bills paid", "Share so far", "Paid so far", settled ? "Reduced so far" : "Difference"], out.reverse()));
    } else {
      const svg = el("svg:svg", {role: "img", "aria-label": "Your share and the bills you paid, running totals. Table lists them by month."});
      card.append(el("div", {class: "legend", style: {margin: "4px 0 6px"}},
        el("span", {}, el("i", {style: {background: "var(--c-imaging)"}}), "Your share per insurers, running total"),
        el("span", {}, el("i", {style: {background: "var(--c-procedure)"}}), "Medical bills you paid, running total")), svg);
      requestAnimationFrame(() => drawPaidCum(svg, claimRows, bills, settled));
    }
  }
  // breakdowns
  const two = el("div", {class: "ov-cols", style: {marginTop: "12px"}});
  const g1 = el("div", {}, el("h3", {class: "sub", text: "What it was for"})), g2 = el("div", {}, el("h3", {class: "sub", text: "Who was paid"}));
  two.append(g1, g2); card.append(two);
  const grp = keyFn => { const m = new Map(); for (const r of counted) { const k = keyFn(r), g = m.get(k) || {key: k, n: 0, v: 0}; g.n++; g.v += r.counted; m.set(k, g); } return [...m.values()].sort((a, b) => b.v - a.v); };
  const pick = k => { paidState.q = paidState.q === k ? "" : k; renderCosts(); $("#paidTable").scrollIntoView({behavior: smoothly(), block: "nearest"}); };
  const bars = (host, groups) => {
    const max = Math.max(1, ...groups.map(g => g.v));
    for (const g of groups) host.append(el("div", {class: "ov-row", style: {gridTemplateColumns: "1fr auto", background: paidState.q === g.key ? "var(--hover)" : null}, onclick: () => pick(g.key)},
      el("span", {class: "n", style: {fontWeight: 500}, text: g.key}), el("span", {class: "v", text: usd(g.v)}),
      el("span", {class: "s", style: {display: "flex", alignItems: "center"}},
        el("span", {style: {display: "inline-block", height: "8px", width: Math.max(0, g.v / max * 72) + "%", background: "var(--c-procedure)", flex: "none", borderRadius: "2px"}}),
        el("span", {style: {marginLeft: "8px", color: "var(--muted)"}, text: `${g.n} amount${g.n === 1 ? "" : "s"}`}))));
  };
  bars(g1, grp(r => r.group)); bars(g2, grp(r => r.who || "(no payee)"));
  // every amount, with where it came from
  const fil = paidState.q;
  const list = rows.filter(r => !fil || r.group === fil || (r.who || "(no payee)") === fil || r.hsa === fil)
    .sort((a, b) => b.d.localeCompare(a.d) || a.type.localeCompare(b.type));
  const tw = el("div", {id: "paidTable", tabindex: -1, style: {marginTop: "12px"}});
  // filtered (HSA only, or a row above picked): what it keeps, with Reset filters, the total for those shown (#190)
  const kept = fil === "HSA" || fil === "FSA" ? `paid with an ${fil}` : rows.some(r => r.group === fil) ? `for ${fil}` : `paid to ${fil}`;
  tw.append(el("div", {class: "toolbar", style: {margin: "0 0 6px"}},
    el("span", {class: "hint", style: {margin: 0}, text: fil ? `For the amounts shown: ${usd(sum(list))} counted` : `${list.length} amount${list.length === 1 ? "" : "s"} · ${usd(sum(list))} counted`}),
    ...["HSA", "FSA"].filter(t => all.some(r => r.hsa === t)).map(t => el("button", {class: "toggle", type: "button", "aria-pressed": fil === t, text: `${t} only`,   // on/off chips (#193)
      onclick: () => { paidState.q = fil === t ? "" : t; renderCosts(); }}))),
    filterLine(fil ? [`Showing ${list.length.toLocaleString()} of ${rows.length.toLocaleString()} amounts`, kept] : [],
      () => { paidState.q = ""; renderCosts(); }, () => $("#paidTable .toggle") || $("#paidTable")));
  const toggle = r => { paidState.open = paidState.open === oopKey(r) ? null : oopKey(r); renderCosts(); const e = $("#oopEdit"); if (e) e.scrollIntoView({block: "nearest"}); };
  tw.append(el("div", {class: "tablewrap", style: {maxHeight: "420px"}}, el("table", {class: "data oop"},
    el("thead", {}, el("tr", {}, ["Date", "Paid to", "For", "Where the number came from", "HSA/FSA", "Amount"].map(h => el("th", {text: h})))),
    el("tbody", {}, list.map(r => el("tr", {class: "link" + (r.dup != null ? " oop-dup" : ""), "data-oop": oopKey(r), tabindex: 0,
      "aria-expanded": paidState.open === oopKey(r) ? "true" : "false",
      onclick: () => toggle(r), onkeydown: ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(r); } }},
      el("td", {style: {whiteSpace: "nowrap"}, text: fmtD(dayTs(r.d))}), el("td", {text: r.who || "—"}),
      el("td", {title: r.memo || null, text: r.type === "claim" ? `${r.group} · ${r.claim.title}` : r.group + (r.claim ? " · paid a claim" : "")}),
      el("td", {text: srcText(r) + (r.receipt != null ? " · receipt" : "")}),
      el("td", {text: r.hsa || ""}),
      el("td", {style: {fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap", color: r.amount < 0 ? "var(--good-text)" : null},
        text: (r.amount < 0 ? `${usd2(-r.amount)} refund` : usd2(r.amount)) + (r.dup != null ? " · counted once" : "")})))))));
  const open = paidState.open && rows.find(r => oopKey(r) === paidState.open);
  if (open) tw.append(oopEditor(open));
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
/* the claims' costs as bars, one per month or per year (by), every one from the first shown to the last */
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
function drawCostBars(svg, rows, by = "month") {
  if (!rows.length) { svg.replaceChildren(); return; }
  const months = costBuckets(rows, by), yearly = by === "year";
  const keys = [...months.keys()];
  const w = Math.max(320, svg.parentElement.clientWidth - 32), h = 220, L = 64, R = w - 10, T = 10, B = h - 24;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("height", h);
  const max = Math.max(1, ...[...months.values()].map(m => m.paid + m.owe)) * 1.08;
  const slot = (R - L) / keys.length, bw = Math.max(2, Math.min(24, slot * .7));
  const x = i => L + slot * i + slot / 2, y = v => B - v / max * (B - T);
  const g = [];
  // hospital stays as shaded month slots
  keys.forEach((k, i) => { const a = k + (yearly ? "-01-01" : "-01"), b = k + (yearly ? "-12-31" : "-31"); if (STAYS.some(s => isoDay(s.ts) <= b && isoDay(s.te) >= a)) g.push(el("svg:rect", {x: L + slot * i, y: T, width: slot, height: B - T, fill: "var(--stay)"})); });
  for (const v of niceTicks(0, max, 4)) { g.push(el("svg:line", {class: "gridline", x1: L, x2: R, y1: y(v), y2: y(v)})); g.push(el("svg:text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", text: v >= 1e6 ? `$${(v / 1e6).toFixed(v % 1e6 ? 1 : 0)}M` : v >= 1e3 ? `$${Math.round(v / 1e3)}K` : `$${v}`})); }
  g.push(el("svg:line", {x1: L, x2: R, y1: B, y2: B, stroke: "var(--axis)"}));
  const every = Math.ceil(keys.length / Math.max(2, Math.floor((R - L) / 56)));
  keys.forEach((k, i) => {
    const m = months.get(k), cx = x(i);
    const yp = y(m.paid), yo = y(m.paid + m.owe);
    if (m.paid > 0) g.push(el("svg:rect", {x: cx - bw / 2, y: yp, width: bw, height: Math.max(0, B - yp), fill: "var(--c-lab)", rx: m.owe > 0 && B - yo > 3 ? 0 : 2}));
    if (m.owe > 0) g.push(el("svg:rect", {x: cx - bw / 2, y: yo, width: bw, height: Math.max(1, yp - yo - (m.paid > 0 ? 1 : 0)), fill: "var(--c-imaging)", rx: 2}));
    if (i % every === 0) g.push(el("svg:text", {x: cx, y: h - 6, "text-anchor": "middle", text: yearly ? k : zdate(dayTs(k + "-15")).toLocaleDateString(undefined, {month: "short", year: "2-digit"})}));
    const hit = el("svg:rect", {x: L + slot * i, y: T, width: slot, height: B - T, fill: "transparent", style: "cursor:pointer"});
    hit.addEventListener("pointermove", ev => showTip([el("div", {class: "v", text: usd(m.paid + m.owe)}), el("div", {class: "s", text: yearly ? k : zdate(dayTs(k + "-15")).toLocaleDateString(undefined, {month: "long", year: "numeric"})}),
      el("div", {class: "s"}, el("span", {class: "k", style: {background: "var(--c-lab)"}}), `Insurance paid ${usd(m.paid)}`),
      el("div", {class: "s"}, el("span", {class: "k", style: {background: "var(--c-imaging)"}}), `Your share ${usd(m.owe)}`),
      el("div", {class: "s", text: `${m.n} claims · billed ${usd(m.billed)}`})], ev));
    hit.addEventListener("pointerleave", hideTip);
    g.push(hit);
  });
  svg.replaceChildren(...g);
}


