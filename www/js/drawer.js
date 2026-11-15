/* Sanguity · detail drawer: one panel for every kind of event.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- detail drawer */
const drawer = $("#drawer");
let drawerReturn = null;
function closeDrawer() {
  if (drawer.classList.contains("open") && drawer.contains(document.activeElement) && drawerReturn?.isConnected) drawerReturn.focus({preventScroll: true});
  drawerReturn = null; drawer.classList.remove("open");
  setTimeout(() => { if (!drawer.classList.contains("open")) $("#dbody").replaceChildren(); }, 250);   // unload any PDF / document once it has slid away
  drawer.setAttribute("aria-hidden", "true"); state.selEv = null; renderTimeline(); document.querySelectorAll(".row.sel").forEach(r => r.classList.remove("sel")); }
$("#dclose").onclick = closeDrawer;
function setWide(on) { drawer.classList.toggle("wide", on); $("#dwide").setAttribute("aria-pressed", on); $("#dwide").title = on ? "Narrower panel" : "Wider panel"; try { localStorage.setItem("ht-wide", on ? "1" : ""); } catch (e) {} }
$("#dwide").onclick = () => setWide(!drawer.classList.contains("wide"));
try { if (localStorage.getItem("ht-wide")) setWide(true); } catch (e) {}
addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
function docFrame(html, h = 420) {
  const f = el("iframe", {class: "doc", sandbox: "allow-popups", style: {height: h + "px"}});
  f.srcdoc = `<!doctype html><meta charset=utf-8><base target=_blank><style>body{font:14px/1.45 system-ui,sans-serif;color:#111;margin:14px}table{border-collapse:collapse}td,th{padding:2px 6px}</style>${html}`;
  f.onload = () => { try { f.style.height = Math.min(1600, f.contentDocument.body.scrollHeight + 30) + "px"; } catch (e) {} };
  return f;
}
function resultTable(rows) {
  if (!rows || !rows.length) return null;
  return el("div", {class: "tablewrap", style: {maxHeight: "none"}}, el("table", {class: "data"},
    el("thead", {}, el("tr", {}, ["Component", "Value", "Ref range", ""].map(h => el("th", {text: h})))),
    el("tbody", {}, rows.map(([n, v, u, rr, f]) => el("tr", {class: SERIES.some(s => s.name === n && s.n) ? "link" : "", title: "Show trend", onclick: () => { if (SERIES.some(s => s.name === n && s.n)) showSeries(n); }},
      el("td", {text: n}), el("td", {class: f ? "abn" : "", text: `${v ?? ""} ${u || ""}`.trim()}), el("td", {text: rr || ""}), el("td", {class: f ? "abn" : "", text: f ? (f === "H" ? "▲ High" : "▼ Low") : ""}))))));
}
function resultBody(d) {
  const out = [];
  const kv = [["Order", d.order], ["Collected", d.collected], ["Resulted", d.resulted], ["Ordered by", d.provider], ["Status", d.status], ["Specimen", d.specimen], ["Lab", d.lab], ["Diagnosis", (d.dx || []).join("; ")]].filter(x => x[1]);
  out.push(el("dl", {class: "kv"}, kv.flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: v})])));
  const t = resultTable(d.rows); if (t) out.push(el("div", {class: "sect", text: "Results"}), t);
  for (const [k, h, isHtml] of d.report || []) out.push(el("div", {class: "sect", text: k === "combinedRTFNarrativeImpression" ? "Report" : k}),
    isHtml ? docFrame(h) : el("pre", {class: "report", text: h.replace(/\r\n/g, "\n").trim()}));
  if (d.comments && d.comments.length) out.push(el("div", {class: "sect", text: "Provider comments"}), ...d.comments.map(c => el("p", {text: c})));
  return out;
}
function openEvent(e) {
  if (!e) return;
  const d = DATA.details[e.id] || {}, c = CAT_OF[e.k] || CATS[2];
  state.selEv = e.id;
  $("#dkey").style.background = c.color; $("#dtitle").textContent = e.title;
  $("#dsub").textContent = `${KIND_LABEL[e.k]} · ${e.end && e.te - e.ts > DAY ? fmtD(e.ts) + " – " + fmtD(e.te) : fmtDT(e.ts)}${e.sub ? " · " + e.sub : ""}`;
  const body = $("#dbody"); body.replaceChildren(); body.scrollTop = 0; body.classList.remove("fill");
  if (d.type === "result") body.append(...resultBody(d));
  else if (d.type === "labday") {
    body.append(el("div", {}, el("button", {class: "btn", text: "Show this day in Lab trends", onclick: () => { setRange(e.ts - 45*DAY, e.ts + 45*DAY); switchTab("labs"); }})));
    for (const id of d.ids) {
      const r = DATA.details[id], ev2 = byId.get(id); if (!r) continue;
      const abn = (r.rows || []).filter(x => x[4]).length;
      const det = el("details", {class: "res", open: d.ids.length <= 3 || null}, el("summary", {}, el("span", {text: `${r.order || ev2.title}`}), el("span", {class: "spacer"}), el("span", {style: {color: "var(--muted)", fontSize: "12px", fontWeight: 400}, text: fmtDT(ev2.ts).split(", ").pop()}), abn ? el("span", {class: "badge abn", text: abn}) : null), el("div", {}, ...resultBody(r)));
      body.append(det);
    }
  } else if (d.type === "conversation") {
    for (const m of d.messages) {
      const html = /<[a-z][\s\S]*>/i.test(m.body);
      body.append(el("div", {class: "msg"}, el("div", {class: "mh"}, el("b", {text: m.from}), " · " + fmtDT(m.t)),
        html ? docFrame(m.body, 200) : el("pre", {text: m.body.replace(/\r\n/g, "\n").trim()}),
        m.att && m.att.length ? el("div", {class: "mh", text: "Attachments: " + m.att.join(", ")}) : null));
    }
  } else if (d.type === "letter") body.append(docFrame(d.html, 600));
  else if (d.type === "file") {
    body.append(el("div", {class: "btns"}, el("a", {class: "btn", href: d.url, target: "_blank", rel: "noopener", text: "Open in new tab"}), el("a", {class: "btn", href: d.url, download: "", text: "Download"})));
    // documents fill the panel's remaining height so the viewer's own scrollbar and toolbar stay on screen
    if (d.ext === "html") { body.classList.add("fill"); body.append(el("iframe", {class: "doc", src: d.url, sandbox: "", title: e.title})); }
    else if (["jpg","jpeg","png","gif"].includes(d.ext)) body.append(el("img", {src: d.url, alt: e.title, style: {maxWidth: "100%", borderRadius: "8px"}}));
    else if (d.ext === "pdf") { body.classList.add("fill"); body.append(el("iframe", {class: "doc", src: d.url + (d.url.includes("#") ? "" : "#view=FitH"), title: e.title})); }
    else if (d.ext === "tif" || d.ext === "tiff") body.append(el("p", {class: "hint", style: {color: "var(--ink2)"}, text: "Scanned TIFF document — browsers can't preview TIFF; use Download and open it in Photos or a PDF viewer."}));
  } else if (d.type === "stay") {
    body.append(d.discharge
      ? el("dl", {class: "kv"}, el("dt", {text: "Admitted"}), el("dd", {text: fmtDT(e.ts)}), el("dt", {text: "Discharged"}), el("dd", {text: fmtDT(e.te)}),
          d.where ? el("dt", {text: "Where"}) : null, d.where ? el("dd", {text: d.where}) : null,
          d.transfer_from ? el("dt", {text: "Transferred from"}) : null, d.transfer_from ? el("dd", {text: d.transfer_from}) : null,
          d.transfer_to ? el("dt", {text: "Transferred to"}) : null, d.transfer_to ? el("dd", {text: d.transfer_to}) : null,
          el("dt", {text: "Source"}), el("dd", {text: d.how || ""}))
      : el("dl", {class: "kv"}, el("dt", {text: "Admitted"}), el("dd", {text: d.admit}), el("dt", {text: "Last result"}), el("dd", {text: fmtDT(d.lastResult)}), el("dt", {text: "Inpatient results"}), el("dd", {text: d.results})),
      d.discharge ? null : el("p", {class: "hint", style: {color: "var(--ink2)"}, text: "Estimated: no admission record was available, so the end date is the last inpatient lab result."}),
      el("button", {class: "btn", text: "Zoom timeline to this stay", onclick: () => { setRange(e.ts - 3*DAY, e.te + 3*DAY); closeDrawer(); }}),
      el("button", {class: "btn", text: "Lab trends during this stay", onclick: () => { setRange(e.ts - 3*DAY, e.te + 3*DAY); switchTab("labs"); closeDrawer(); }}));
  } else if (d.type === "exercise") {
    body.append(el("dl", {class: "kv"}, ...[["Activity", d.kind], ["Started", fmtDT(e.ts)], ["Ended", fmtDT(e.te)], ["Duration", d.minutes + " min"], ["Source", "Watch via Health Connect"]].filter(x => x[1]).flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: v})])),
      el("button", {class: "btn", text: "Health data around this day", onclick: () => openHealth(e.ts - 14*DAY, e.ts + 14*DAY)}));
  } else if (d.type === "visit") {
    body.append(el("dl", {class: "kv"}, ...[["Provider", d.who], ["Location", d.where], ["Diagnosis", d.dx], ["Source", d.src]].filter(x => x[1]).flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: v})])));
    if (d.note) body.append(el("p", {class: "hint", style: {color: "var(--ink2)"}, text: d.note}));
    if (d.procs && d.procs.length) body.append(el("div", {class: "sect", text: "Recorded that day"}), el("ul", {style: {margin: 0, paddingLeft: "18px"}}, ...d.procs.map(p => el("li", {text: p}))));
    const sameDay = EV.filter(x => x.k === "document" && isoDay(x.ts) === isoDay(e.ts));
    if (sameDay.length) body.append(el("div", {class: "sect", text: "Documents from that day"}), ...sameDay.map(x => el("button", {class: "btn", text: x.title, onclick: () => openEvent(x)})));
  } else if (d.type === "claim") {
    const st = (d.status || "").replace(/_/g, " ").toLowerCase();
    body.append(el("dl", {class: "kv"}, ...[["Insurer", d.insurer], ["Type", d.kind], ["Provider", d.provider], ["Drug", d.drug], ["Claim #", d.claimId], ["Status", st],
      ["Network", (d.network || "").replace(/_/g, " ").toLowerCase()], ["Billed", usd(d.billed)], ["Plan paid", usd(d.paid)], ["Your share", usd(d.owe)]]
      .filter(x => x[1]).flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: v, style: k === "Status" && /denied/.test(st) ? {color: "var(--crit)", fontWeight: 650} : null})])));
    if (d.lines && d.lines.length) body.append(el("div", {class: "sect", text: "Services billed"}), el("div", {class: "tablewrap", style: {maxHeight: "none"}}, el("table", {class: "data"},
      el("thead", {}, el("tr", {}, ["Date", "Service", "Code", "Billed"].map(h => el("th", {text: h})))),
      el("tbody", {}, d.lines.map(l => el("tr", {}, el("td", {text: l.date || ""}), el("td", {}, l.desc || "", ...(l.denied || []).map(x => el("div", {style: {color: "var(--crit)", fontSize: "12px"}, text: "Denied: " + x}))),
        el("td", {text: l.code || ""}), el("td", {text: l.billed ? usd(l.billed) : ""})))))));
    if (/denied/.test(st)) body.append(el("p", {class: "hint", style: {color: "var(--ink2)"}, text: "\"Provider denied\" usually means the provider must correct or resubmit the claim and you aren't billed for it; \"denied\" can mean you are. The EOB on your insurer's site has the exact reason."}));
  } else if (d.type === "med") {
    body.append(el("dl", {class: "kv"}, ...[["Instructions", d.instructions], ["Status", d.status], ["Listed since", fmtD(e.ts)], ["Source", d.src]].filter(x => x[1]).flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: v})])),
      el("p", {class: "hint", style: {color: "var(--ink2)"}, text: "From the clinic's medication list. The date is when it was recorded there, not necessarily when you started it."}));
  }
  if (!drawer.classList.contains("open")) drawerReturn = document.activeElement;
  drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
  $("#dclose").focus({preventScroll: true});
  renderTimeline(); document.querySelectorAll(".row").forEach(r => r.classList.remove("sel"));
}

