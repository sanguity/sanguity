/* Sanguity · detail drawer: one panel for every kind of event.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- detail drawer */
const drawer = $("#drawer");
let drawerReturn = null;
function closeDrawer() {
  const back = drawer.classList.contains("open") && drawer.contains(document.activeElement) && drawerReturn?.isConnected ? drawerReturn : null;
  drawerReturn = null; drawer.classList.remove("open"); drawerCover();
  if (back) back.focus({preventScroll: true});   // once the page behind isn't inert any more
  setTimeout(() => { if (!drawer.classList.contains("open")) $("#dbody").replaceChildren(); }, 250);   // unload any PDF / document once it has slid away
  drawer.setAttribute("aria-hidden", "true"); state.selEv = null; renderTimeline(); document.querySelectorAll(".row.sel").forEach(r => r.classList.remove("sel")); }
$("#dclose").onclick = closeDrawer;
/* while the drawer covers the whole page (on a phone, or wide on a small screen), the page behind it is inert, so Tab
   and a screen reader stay in the drawer; closed, the drawer itself is inert, out of Tab's way off-screen (#209) */
let drawerBehind = [];
function drawerCover() {
  const open = drawer.classList.contains("open");
  drawer.inert = !open;
  for (const x of drawerBehind) x.inert = false;
  drawerBehind = open && drawer.offsetWidth >= document.documentElement.clientWidth
    ? [...document.body.children].filter(x => x !== drawer && !x.inert && !x.matches("script, .sg-live")) : [];
  for (const x of drawerBehind) x.inert = true;
}
drawerCover();
addEventListener("resize", drawerCover);
function setWide(on) { drawer.classList.toggle("wide", on); drawerCover(); $("#dwide").setAttribute("aria-pressed", on); $("#dwide").title = on ? "Narrower panel" : "Wider panel"; try { localStorage.setItem("ht-wide", on ? "1" : ""); } catch (e) {} }
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
    el("tbody", {}, rows.map(([n, v, u, rr, f], i) => el("tr", {class: SERIES.some(s => s.name === n && s.n) ? "link" : "", title: "Show trend", onclick: () => { if (SERIES.some(s => s.name === n && s.n)) showSeries(n); }},
      el("td", {title: renamedTitle("lab", n)}, nice(n), nice(n) !== n ? el("small", {class: "muted", text: ` (${n})`}) : null,   // #38
        SERIES.some(s => s.name === n && s.n) ? renameButton("lab", n) : null), el("td", {class: f ? "abn" : "", text: `${v ?? ""} ${u || ""}`.trim()}, ...rowOrig(rows[i])), el("td", {text: rr || ""}), el("td", {class: f ? "abn" : "", text: f ? (f === "H" ? "▲ High" : "▼ Low") : ""}))))));
}
/* #41: a converted row's value and range as reported (units.js), on its own line under the value */
function rowOrig(row) {
  return row.orig ? [el("small", {class: "orig", text: `Reported ${`${row.orig[0] ?? ""} ${row.orig[1] || ""}`.trim()}${row.orig[2] ? ", ref " + row.orig[2] : ""}`})] : [];
}
function resultBody(d, eid) {
  const out = [];
  const kv = [["Order", d.order], ["Collected", d.collected], ["Resulted", d.resulted], ["Ordered by", d.provider], ["Status", d.status], ["Specimen", d.specimen], ["Lab", d.lab], ["Diagnosis", (d.dx || []).join("; ")]].filter(x => x[1]);
  out.push(el("dl", {class: "kv"}, kv.flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: v})])));
  const t = resultTable(d.rows); if (t) out.push(el("div", {class: "sect", text: "Results"}), t);
  const same = sameNotes(eid, d); if (same) out.push(same);   // the same result from another source (same.js)
  for (const [k, h, isHtml] of d.report || []) out.push(el("div", {class: "sect", text: k === "combinedRTFNarrativeImpression" ? "Report" : k}),
    isHtml ? docFrame(h) : el("pre", {class: "report", text: h.replace(/\r\n/g, "\n").trim()}));
  if (d.comments && d.comments.length) out.push(el("div", {class: "sect", text: "Provider comments"}), ...d.comments.map(c => el("p", {text: c})));
  return out;
}
function openEvent(e) {
  if (!e) return;
  if (e.k === "journal") return openJournal(e);   // journal.js
  const d = DATA.details[e.id] || {}, c = CAT_OF[e.k] || CATS[2];
  state.selEv = e.id;
  $("#dkey").style.background = c.color; $("#dtitle").textContent = e.title;
  $("#dsub").textContent = `${KIND_LABEL[e.k]} · ${e.end && e.te - e.ts > DAY ? fmtD(e.ts) + " – " + fmtD(e.te) : fmtDT(e.ts)}${e.sub ? " · " + e.sub : ""}`;
  const body = $("#dbody"); body.replaceChildren(); body.scrollTop = 0; body.classList.remove("fill");
  if (d.type === "result") body.append(...resultBody(d, e.id));
  else if (d.type === "labday") {
    body.append(el("div", {}, el("button", {class: "btn", text: "Show this day in Lab trends", onclick: () => { setRange(e.ts - 45*DAY, e.ts + 45*DAY); pickTab("labs"); }})));
    for (const id of d.ids) {
      const r = DATA.details[id], ev2 = byId.get(id); if (!r) continue;
      const abn = (r.rows || []).filter(x => x[4]).length;
      const det = el("details", {class: "res", open: d.ids.length <= 3 || null}, el("summary", {}, el("span", {text: `${r.order || ev2.title}`}), el("span", {class: "spacer"}), el("span", {style: {color: "var(--muted)", fontSize: "12px", fontWeight: 400}, text: fmtDT(ev2.ts).split(", ").pop()}), abn ? el("span", {class: "badge abn", text: abn}) : null), el("div", {}, ...resultBody(r, id), eventSource(ev2)));
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
    // HTML records: no scripts (the sandbox has no allow-scripts, and the server sends script-src 'none'), but
    // same-origin, so the record can load the pictures and stylesheet saved beside it (an opaque origin can't)
    if (d.ext === "html" || d.ext === "htm") { body.classList.add("fill"); body.append(el("iframe", {class: "doc", src: d.url, sandbox: "allow-same-origin", title: e.title})); }
    else if (["jpg","jpeg","png","gif"].includes(d.ext)) body.append(el("img", {src: d.url, alt: e.title, style: {maxWidth: "100%", borderRadius: "8px"}}));
    else if (d.ext === "pdf") { body.classList.add("fill"); body.append(el("iframe", {class: "doc", src: d.url + (d.url.includes("#") ? "" : "#view=FitH"), title: e.title})); }
    else if (d.ext === "tif" || d.ext === "tiff") body.append(el("p", {class: "hint", style: {color: "var(--ink2)"}, text: "Scanned TIFF document — browsers can't preview TIFF; use Download and open it in Photos or a PDF viewer."}));
  } else if (d.type === "stay") {
    body.append(d.discharge
      ? el("dl", {class: "kv"}, el("dt", {text: "Admitted"}), el("dd", {text: fmtDT(e.ts)}), el("dt", {text: "Discharged"}), el("dd", {text: fmtDT(e.te)}),
          d.where ? el("dt", {text: "Where"}) : null, d.where ? el("dd", {text: d.where}) : null,
          d.transfer_from ? el("dt", {text: "Transferred from"}) : null, d.transfer_from ? el("dd", {text: d.transfer_from}) : null,
          d.transfer_to ? el("dt", {text: "Transferred to"}) : null, d.transfer_to ? el("dd", {text: d.transfer_to}) : null,
          d.admission ? el("dt", {text: "Admission"}) : null, d.admission ? el("dd", {text: d.admission}) : null,
          el("dt", {text: "Source"}), el("dd", {text: d.how || ""}))
      : el("dl", {class: "kv"}, el("dt", {text: "Admitted"}), el("dd", {text: d.admit}), el("dt", {text: "Last result"}), el("dd", {text: fmtDT(d.lastResult)}), el("dt", {text: "Inpatient results"}), el("dd", {text: d.results})),
      d.note ? el("p", {class: "hint", style: {color: "var(--ink2)"}, text: d.note}) : null,
      d.discharge ? null : el("p", {class: "hint", style: {color: "var(--ink2)"}, text: "Estimated: no admission record was available, so the end date is the last inpatient lab result."}),
      el("button", {class: "btn", text: "Zoom timeline to this stay", onclick: () => { setRange(e.ts - 3*DAY, e.te + 3*DAY); closeDrawer(); }}),
      el("button", {class: "btn", text: "Lab trends during this stay", onclick: () => { setRange(e.ts - 3*DAY, e.te + 3*DAY); pickTab("labs"); closeDrawer(); }}));
  } else if (d.type === "exercise") {
    body.append(el("dl", {class: "kv"}, ...[["Activity", d.kind], ["Started", fmtDT(e.ts)], ["Ended", fmtDT(e.te)], ["Duration", d.minutes + " min"], ["Source", "Watch via Health Connect"]].filter(x => x[1]).flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: v})])),
      el("button", {class: "btn", text: "Body and activity around this day", onclick: () => openHealth(e.ts - 14*DAY, e.ts + 14*DAY)}));
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
    const typed = d.src === "Typed in";   // hand entry (import page): the dates are the ones typed
    body.append(el("dl", {class: "kv"}, ...[["Instructions", d.instructions], ["Status", d.status], [typed ? "Started" : "Listed since", fmtD(e.ts)],
      ["Stopped", d.end ? fmtD(d.end) : ""], ["Prescribed by", d.prescriber], ["Source", d.src]].filter(x => x[1]).flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: v})])),
      typed ? null : el("p", {class: "hint", style: {color: "var(--ink2)"}, text: "From the clinic's medication list. The date is when it was recorded there, not necessarily when you started it."}));
  }
  { const mh = medHistoryAction(e); if (mh) body.append(mh); }   // a medication's whole history (meds.js, #50)
  const src = eventSource(e); if (src) body.append(src);   // where it came from (sources.js)
  body.append(journalAddAction(e));   // "Add a journal note" for this day (journal.js)
  if (e.k !== "exercise") body.append(editPanel(e));
  showDrawer();
  renderTimeline(); document.querySelectorAll(".row").forEach(r => r.classList.remove("sel"));
}
function showDrawer() {
  if (!drawer.classList.contains("open")) drawerReturn = document.activeElement;
  drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); drawerCover();
  $("#dclose").focus({preventScroll: true});
}

/* ---------------------------------------------------------------- corrections (edits, server/edits.py)
   Owners and editors can hide, rename, re-date, un-stay or merge the open event; everyone sees what was edited.
   Edits are applied on the server, so after a change the page loads the records again and reopens the panel. */
const canEdit = () => !!PROFILE && (PROFILE.role === "owner" || PROFILE.role === "editor");
const editsOf = e => (DATA.edits || []).filter(x => x.ids.includes(e.id));
const quoted = t => `“${t || "an event"}”`;
function describeEdit(x) {
  const was = x.was || {}, v = x.value || {};
  if (x.action === "hide") return `Hidden: ${quoted(was.title)}`;
  if (x.action === "rename") return `Renamed ${quoted(was.title)} to ${quoted(v.title)}`;
  if (x.action === "date") return `Date of ${quoted(was.title)} changed${was.t ? " from " + fmtDT(was.t) : ""} to ${fmtDT(v.t)}`;
  if (x.action === "not_stay") return `${quoted(was.title)} marked as not a hospital stay`;
  if (x.action === "merge") return `${quoted((x.other || v.other || {}).title)} merged into ${quoted(was.title)}`;
  return "Edited";
}
function afterEdit(reopen) {
  try { sessionStorage.setItem("sg-after-edit", JSON.stringify(reopen)); } catch (e) {}
  location.reload();
}
async function sendEdit(path, opts, msg, reopen) {
  sayIn(msg, "Saving…");
  try {
    const r = await api(`profiles/${PROFILE.id}/edits${path}`, opts);
    if (!r.ok) { const j = await r.json().catch(() => ({})); sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save the edit (${r.status}).`, true); return; }
    afterEdit(reopen);
  } catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); }
}
function undoButton(x, msg, reopen) {
  return el("button", {class: "btn small", type: "button", text: "Undo", "data-edit": x.id, title: "Undo this edit",
    onclick: () => sendEdit("/" + x.id, {method: "DELETE"}, msg, reopen)});
}
function editPanel(e) {
  const box = el("div", {class: "edits"}), msg = el("p", {class: "hint edit-msg", role: "status"});
  const mine = editsOf(e);
  if (mine.length) box.append(el("div", {class: "edited"}, el("b", {text: "Edited"}),
    ...mine.map(x => el("div", {class: "edit-line"}, el("span", {text: describeEdit(x) + (x.by ? ` · by ${x.by}` : "")}),
      canEdit() ? undoButton(x, msg, {event: e.id}) : null))));
  if (!canEdit()) return mine.length ? box : el("span");
  const forms = el("div", {class: "edit-form"});
  const post = (body, reopen = {event: e.id}) => sendEdit("", {method: "POST", body: JSON.stringify({event: e.id, ...body})}, msg, reopen);
  const form = (label, input, onsave) => {
    forms.replaceChildren(el("label", {}, el("span", {text: label}), input),
      el("div", {}, el("button", {class: "btn primary", type: "button", text: "Save", onclick: onsave}),
        el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => forms.replaceChildren()})));
    input.focus();
  };
  const acts = el("div", {class: "edit-acts"},
    el("button", {class: "btn", type: "button", text: "Hide", title: "Leave this event out of the records", onclick: () => post({action: "hide"}, {edits: true})}),
    el("button", {class: "btn", type: "button", text: "Rename", onclick: () => {
      const i = el("input", {type: "text", maxlength: 300, "aria-label": "New title"}); i.value = e.title;
      form("New title", i, () => post({action: "rename", title: i.value}));
    }}),
    el("button", {class: "btn", type: "button", text: "Fix date", onclick: () => {
      const i = el("input", {type: "datetime-local", "aria-label": "New date and time"}); i.value = String(e.t || "").slice(0, 16);
      form("Starts", i, () => post({action: "date", t: i.value}));
    }}),
    e.k === "stay" ? el("button", {class: "btn", type: "button", text: "Not a hospital stay", onclick: () => post({action: "not_stay"})}) : null,
    el("button", {class: "btn", type: "button", text: "Merge with…", onclick: () => {
      const near = DATA.events.filter(x => x.id !== e.id && x.k !== "exercise" && Math.abs(x.ts - e.ts) <= 7 * DAY)
        .sort((a, b) => Math.abs(a.ts - e.ts) - Math.abs(b.ts - e.ts)).slice(0, 50);
      if (!near.length) { sayIn(msg, "No other event within a week of this one to merge with."); return; }
      const sel = el("select", {"aria-label": "Event to merge into this one"},
        ...near.map(x => el("option", {value: x.id, text: `${fmtDT(x.ts)} · ${KIND_LABEL[x.k] || x.k} · ${x.title}`})));
      form("Fold this event into the open one (it keeps its own title and date)", sel, () => post({action: "merge", other: +sel.value}));
    }}),
    (DATA.edits || []).length + (DATA.same || []).length ? el("button", {class: "btn", type: "button", text: `All edits (${(DATA.edits || []).length + (DATA.same || []).length})`, onclick: openEdits}) : null);
  box.append(el("div", {class: "sect", text: "Correct this event"}), acts, forms, msg);
  return box;
}
/* every edit to these records, including those whose event is gone (its import undone, or changed at the source) */
function openEdits() {
  const list = DATA.edits || [], msg = el("p", {class: "hint edit-msg", role: "status"}), links = sameLines(msg);
  state.selEv = null;
  $("#dkey").style.background = "var(--muted)"; $("#dtitle").textContent = "Edits to these records";
  const n = list.length + links.length;
  $("#dsub").textContent = n ? `${n} edit${n === 1 ? "" : "s"}, applied every time the records load` : "No edits";
  const body = $("#dbody"); body.replaceChildren(); body.scrollTop = 0; body.classList.remove("fill");
  body.append(el("div", {class: "edits"}, ...list.map(x => {
    const ev = x.found && x.action !== "hide" ? byId.get(x.ids[0]) : null;
    return el("div", {class: "edit-line"},
      el("span", {}, describeEdit(x), x.by ? ` · by ${x.by}` : "", x.found ? null : el("em", {class: "missing", text: " · not found in the current records"})),
      ev ? el("button", {class: "btn small", type: "button", text: "Open", onclick: () => openEvent(ev)}) : null,
      canEdit() ? undoButton(x, msg, {edits: true}) : null);
  }), ...links, msg));
  showDrawer();
  renderTimeline();
}
/* after the reload an edit causes: reopen what was open */
function drawerResume() {
  let r = null;
  try { r = JSON.parse(sessionStorage.getItem("sg-after-edit") || "null"); sessionStorage.removeItem("sg-after-edit"); } catch (e) {}
  if (!r) return;
  if (r.event && byId.has(r.event)) openEvent(byId.get(r.event));
  else if (r.alt && byId.has(r.alt)) openEvent(byId.get(r.alt));   // a result linked away: the other side's day
  else openEdits();
}

