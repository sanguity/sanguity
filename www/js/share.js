/* Sanguity · a share link's page (#47): the part of a profile its owner chose to show, read-only, for someone
   without an account (server/share_links.py).

   The token is in this page's address after the #, which the browser never sends to any server: it's posted to
   api/share/view in the request body. The answer holds only what the link shows; nothing here can change anything,
   sign anyone in or reach any other page's data. No cookie is used or set, and nothing is kept in this browser.
   Dates are shown in this browser's own zone and format (dates.js, without anyone's settings).
   For a clinician (#289): a bar of the sections that stays in view, lab results as one table of the latest ones that
   open their histories, labs by the names the profile gives them, and Print (app.css's print rules: the records, not the controls).
   Ten years of records (#491): a long table shows its latest rows, with Show all, which asks the server for the rest (#498). */
"use strict";
const SH_KIND = {visit: "Visit", stay: "Hospital stay", procedure: "Procedure", imaging: "Imaging", study: "Report / study",
  note: "Note", symptom: "Symptom", side_effect: "Side effect", mood: "Mood"};
const shDay = t => t ? fmtD(zParse(String(t).slice(0, 10))) : "";
const shWhen = t => !t ? "" : /T\d\d:\d\d/.test(t) ? fmtDT(zParse(String(t).slice(0, 19))) : shDay(t);
const shUsd = v => v == null ? "" : v.toLocaleString(undefined, {style: "currency", currency: "USD"});

$("#theme").onclick = () => {
  const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  try { localStorage.setItem("ht-theme", dark ? "light" : "dark"); } catch (e) {}
  applyTheme();
};

/* a table that scrolls sideways inside its card on a narrow screen, never the page. printed: a head row naming it, over
   its column names, shown only in print, where each page the table spans repeats its head (#491: a history's pages
   each name its test) */
function shTable(heads, rows, empty, cls = "", name = "", printed = "") {   // cls "measures": stays a table on a phone (#225); else a cell a line where it doesn't fit (.share-cq, #514)
  if (!rows.length) return el("p", {class: "muted", text: empty});
  return scrollBox({class: cls === "measures" ? "share-scroll" : "share-scroll share-cq", "aria-label": name || heads.filter(Boolean).join(", ")}, el("table", {class: ("tbl " + cls).trim()},
    el("thead", {}, printed ? el("tr", {class: "share-printed"}, el("th", {colspan: heads.length, text: printed})) : null,
      el("tr", {}, ...heads.map(h => el("th", {text: h})))),
    el("tbody", {}, ...rows.map(shRow))));
}
// a date kept whole and a time with its AM or PM (keepDates, #509: "10:30 / AM" in Visits; #514: a history's dates wrap)
const shRow = r => el("tr", {}, ...r.map(c => el("td", {}, c && c.nodeType ? c : keepDates(c == null ? "" : String(c)))));
/* a long table, newest first (#491: ten years are 2,000 claims, and 1,000 readings of a home measure). The server sends
   a long section's latest rows and how many it has (#498, SH_CUT: share_links.py FIRST and MANY), said here, with Show
   all, which asks it for the rest (a view logged like the page's), puts them in and gives the first of them focus.
   id: the section; more(): its rows in full, as cells; total: how many there are, when even those are only the
   latest (a measure's block, measures.LINK_MOST) */
let SH_CUT = {};
function shLong(heads, rows, empty, cls, noun, id, more, total = rows.length) {
  const box = shTable(heads, rows, empty, cls), whole = SH_CUT[id] || 0;
  total = Math.max(total, whole);
  const n = k => k.toLocaleString("en-US"), said = k => total > k ? `The latest ${n(k)} of ${n(total)}${noun ? " " + noun : ""}.` : "";
  if (!whole) return total > rows.length ? el("div", {}, box, el("p", {class: "hint", text: said(rows.length)})) : box;
  // #501: the note a live region the button is described by, saying it's loading and why it failed; the button
  // aria-disabled while it loads, not disabled, so focus stays on it; the new rows take focus only if it's still there
  const note = el("p", {class: "hint", role: "status", id: "shMore-" + id, text: said(rows.length)});
  const label = total > whole ? `Show the latest ${n(whole)}` : `Show all ${n(whole)}`;
  const all = el("button", {class: "btn small", type: "button", text: label, "aria-describedby": note.id,
    onclick: async () => {
      if (all.getAttribute("aria-disabled")) return;
      all.setAttribute("aria-disabled", "true");
      all.textContent = note.textContent = "Loading…";
      let got;
      try { got = (await more()).map(shRow); }
      catch (e) {
        note.textContent = e.status === 429 ? "This link has been opened many times in a short while. Try again in a few minutes."
          : "The rest couldn't be loaded. Try again, or reload the page.";
        all.removeAttribute("aria-disabled");
        all.textContent = label;
        return;
      }
      const here = document.activeElement === all, head = tools.closest("section")?.querySelector("h2")?.textContent;
      box.querySelector("tbody").replaceChildren(...got);
      note.textContent = said(got.length);
      (note.textContent ? all : tools).remove();
      // #505: every row in, and focus moved on: said in the page's live line, which stays (the note went with the button)
      if (!here && !note.textContent) announce(`${head ? head + ": " : ""}all ${n(got.length)} ${noun || "rows"} shown.`);
      if (!here) return;
      const first = got[rows.length] || got[0];
      first.tabIndex = -1;
      first.focus();
    }});
  const tools = el("div", {class: "share-tools share-more"}, note, all);
  return el("div", {}, box, tools);
}
/* a long section in full, for its Show all (#498): asked for by the token, like the view */
async function shWhole(id) {
  const r = await fetch("api/share/view", {method: "POST", cache: "no-store", credentials: "omit",
    headers: {"X-Sanguity": "1", "Content-Type": "application/json"}, body: JSON.stringify({token: SH_TOKEN, section: id})});
  if (!r.ok) throw Object.assign(new Error(r.status), {status: r.status});
  return (await r.json())[id];
}

/* a long section's table (#498): its rows as cells, now and once Show all has the rest */
const shList = (id, heads, cells, empty, noun) => list => shLong(heads, list.map(cells), empty, "", noun, id,
  async () => (await shWhole(id)).map(cells));
// a stay's kind before its title, unless the title starts with it (#514: "Hospital stay: Hospital stay · Northfield…")
const shKindOf = v => { const k = v.kind !== "visit" && SH_KIND[v.kind]; return k && !String(v.title || "").toLowerCase().startsWith(k.toLowerCase()) ? k + ": " : ""; };
const SH_DRAW = {
  visits: shList("visits", ["Date", "What", "Details"], v => [v.end ? `${shDay(v.t)} – ${shDay(v.end)}` : shWhen(v.t),
    shKindOf(v) + v.title, v.sub], "No visits or stays in this range.", "visits and stays"),
  procedures: shList("procedures", ["Date", "Kind", "Title"], v => [shDay(v.t), SH_KIND[v.kind] || "", v.title], "None in this range.", "records"),
  medications: list => shTable(["Medication", "Instructions", "From", "Until"], list.map(m => [m.name + (m.status ? ` (${m.status})` : ""),
    m.instructions, shDay(m.since), m.until ? shDay(m.until) : "—"]), "No medications in this range."),
  results: list => list.length ? shLatest(list) : el("p", {class: "muted", text: "No lab results in this range."}),
  appointments: list => shTable(["When", "What", "With", "Where"], list.map(a => [shWhen(a.t), a.title, a.who, a.where]), "No upcoming appointments."),
  journal: list => list.length ? el("div", {}, ...list.map(j => el("div", {class: "share-entry"},
    el("div", {}, el("b", {text: (SH_KIND[j.kind] || "Note") + (j.title ? `: ${j.title}` : "")}),
      // the medication an entry is linked to (#518), whole on the next line if it doesn't fit (#523: "Allopurinol 300 / mg tablet")
      j.med ? " · " : null, j.med ? el("span", {class: "share-med", text: j.med}) : null,
      j.severity ? el("span", {class: "tag", text: `severity ${j.severity}`}) : null,
      j.kind === "mood" ? el("span", {class: "tag", text: [j.mood != null && `mood ${j.mood}/10`, j.energy != null && `energy ${j.energy}/10`].filter(Boolean).join(", ")}) : null,
      el("span", {class: "muted"}, " ", keepDates(shWhen(j.t)))),
    j.where?.length || j.group ? el("p", {class: "muted", text: [j.where?.length && "Where: " + j.where.join(", "), j.group && `Logged with “${j.group}”`].filter(Boolean).join(" · ")}) : null,   // #161
    j.note ? el("p", {class: "share-note", text: j.note}) : null,
    j.photos?.length ? el("div", {class: "share-pics"}, ...j.photos.map((p, i) => shPhoto(p, `Photo ${i + 1} of ${j.photos.length} for ${j.title || "this entry"}`))) : null)))
    : el("p", {class: "muted", text: "No journal entries in this range."}),
  about: a => el("div", {}, el("dl", {class: "share-about"},   // #194: only when the link's owner ticked it
    el("dt", {text: "Birth date"}), el("dd", {text: a.birthDate ? shDay(a.birthDate) : "Not given"}),
    el("dt", {text: "Sex used for reference ranges"}), el("dd", {text: a.sex || "Not given"})),
    shTable(["Date", "Height"], (a.heights || []).map(h => [shDay(h.date), h.text]), "No heights written down in this range.")),
  costs: shList("costs", ["Date", "Service", "Provider", "Billed", "Insurance paid", "Your share"],
    c => [shDay(c.t), c.title, c.provider, shUsd(c.billed), shUsd(c.paid), shUsd(c.owe)], "No claims in this range.", "claims"),
};

/* Lab results (#289): one Latest results table, a row per test (by the profile's name for it, under its panel, labGroups
   in util.js), with its latest result and date, the range and flag printed on that result's report, a sparkline of
   the results in the link's dates and how many there are. A test's name opens its history under it, newest first;
   Open every history opens them all (to print them). A flag is said in words beside ▲/▼, never in red alone. */
const SH_FLAG = {High: "▲ High", Low: "▼ Low"};
const shFlag = f => f ? el("span", {class: "share-flag", text: SH_FLAG[f] || "◆ " + f}) : "";
function shSpark(pts) {   // newest first, as the server sends them; null when fewer than two are numbers
  const got = pts.filter(p => typeof p.v === "number").reverse();
  if (got.length < 2) return null;
  const w = 80, h = 20, ts = got.map(p => zParse(String(p.t).slice(0, 19))), vs = got.map(p => p.v);
  let lo = Math.min(...vs), hi = Math.max(...vs); if (lo === hi) { lo -= 1; hi += 1; }
  const t0 = Math.min(...ts), t1 = Math.max(...ts);
  const x = i => (3 + (ts[i] - t0) / ((t1 - t0) || 1) * (w - 6)).toFixed(1), y = v => (h - 3 - (v - lo) / (hi - lo) * (h - 6)).toFixed(1);
  return el("svg:svg", {class: "share-spark", viewBox: `0 0 ${w} ${h}`, "aria-hidden": "true"},
    el("svg:path", {d: got.map((p, i) => (i ? "L" : "M") + x(i) + "," + y(p.v)).join("")}),
    ...got.map((p, i) => el("svg:circle", {cx: x(i), cy: y(p.v), r: p.flag ? 2.2 : 1.4, class: p.flag ? "out" : null})));
}
let SH_HIST = 0;
function shLatest(list) {
  const groups = labGroups(list, list), heads = ["Test", "Latest", "Range", "Flag", "Trend", "Results"], hists = [];
  const n = list.reduce((a, s) => a + s.results.length, 0);
  const row = s => {
    const last = s.results[0], id = "shareHist" + ++SH_HIST, days = new Set(s.results.map(p => String(p.t).slice(0, 10)));
    const open = el("button", {class: "cell-btn share-open", type: "button", "aria-expanded": false, "aria-controls": id,
      onclick: () => { shOpen(open, hist, open.getAttribute("aria-expanded") !== "true"); follow(); }}, icon("chevron-down"), s.label);
    const hist = el("tr", {class: "share-hist", id, hidden: ""}, el("td", {colspan: heads.length},
      s.label !== s.name ? el("p", {class: "hint", text: `Named “${s.name}” on its reports.`}) : null,
      // a result's flag beside its value, under it where there's no room (#523: at 320 px "◆ Abnormal" in a column of
      // its own put the history 23 px past its box)
      shTable(["Date", "Result", "Reference range"], s.results.map(p => [days.size < s.results.length ? shWhen(p.t) : shDay(p.t),
        p.flag ? el("span", {}, p.value + " ", shFlag(p.flag)) : p.value, p.range]), "", "measures", `${s.label}: every result`,
        `${s.label}${s.unit ? ` (${s.unit})` : ""}: every result`)));
    hists.push([open, hist]);
    return [el("tr", {class: last.flag ? "out" : null},
      el("th", {scope: "row"}, open, s.unit ? el("span", {class: "share-unit", text: s.unit}) : null),
      el("td", {}, el("span", {class: "share-v", text: last.value}), el("span", {class: "share-when", text: shDay(last.t)})),
      el("td", {text: last.range}), el("td", {}, shFlag(last.flag)), el("td", {}, shSpark(s.results)),
      el("td", {class: "share-n", text: String(s.results.length)})), hist];
  };
  const table = el("table", {class: "tbl share-latest"}, el("caption", {class: "vh", text: "Latest results"}),
    el("thead", {}, el("tr", {}, ...heads.map(h => el("th", {scope: "col", text: h})))),
    ...groups.map(g => el("tbody", {}, groups.length > 1 ? el("tr", {class: "share-panel"}, el("th", {scope: "rowgroup", colspan: heads.length, text: g.panel})) : null,
      ...g.labs.flatMap(row))));
  const all = el("button", {class: "btn small", type: "button", "aria-pressed": false, text: "Open every history", onclick: () => {
    const on = all.getAttribute("aria-pressed") !== "true";
    for (const [b, h] of hists) shOpen(b, h, on);
    follow();
  }});
  // pressed while every history is open (#491): with one closed by hand, the next press opens them all again
  const follow = () => all.setAttribute("aria-pressed", String(hists.every(([b]) => b.getAttribute("aria-expanded") === "true")));
  const many = (k, w) => `${k.toLocaleString("en-US")} ${w}${k === 1 ? "" : "s"}`;   // "5,432 results", as the page's other counts (#505)
  return el("div", {}, el("div", {class: "share-tools"}, el("p", {class: "share-count"},
    `The latest of ${many(n, "result")} in ${many(list.length, "test")}.`, el("span", {text: " A test's name opens its history."})), list.length > 1 ? all : null),
    scrollBox({class: "share-scroll"}, table));
}
function shOpen(btn, hist, on) { btn.setAttribute("aria-expanded", String(on)); hist.hidden = !on; }
/* printed (#509): each open history taken out of Latest results' table, a block between tables of its rows (each with
   its head), and put back after. A table's head repeats on each page it spans, so it was over a history alone (most of
   a ten-year print's pages), and a history in a table's row went to the next page whole (app.css). #514: only a long
   one, of more than SH_ROWS results: a short one stays in its row, whole on its page with its test, so the head isn't
   above each test's row after one (three or four times a page) */
let shSplit = null;
const SH_ROWS = 6;
addEventListener("beforeprint", () => {
  const t = document.querySelector("#share-results .share-latest");
  const open = t ? [...t.querySelectorAll(".share-hist:not([hidden])")].filter(r => r.querySelector("tbody")?.rows.length > SH_ROWS) : [];
  if (shSplit || !open.length) return;
  const at = document.activeElement, rows = [...t.tBodies].map(b => [b, [...b.rows]]), out = el("div", {class: "share-split"});
  let seg = null;
  for (const [, rs] of rows) for (const r of rs) {
    if (open.includes(r)) { out.append(el("div", {class: "share-histp"}, ...r.cells[0].childNodes)); r.remove(); seg = null; }
    else if (!out.firstChild) continue;   // (rows before the first stay in its table)
    else if (r.hidden) r.remove();   // a closed history: nothing left at a table's foot to break before
    else {
      if (!seg) out.append(el("table", {class: "tbl share-latest"}, t.tHead.cloneNode(true), seg = el("tbody")));
      seg.append(r);
    }
  }
  shSplit = {rows, open, out, at};
  t.closest(".share-scroll").after(out);
});
addEventListener("afterprint", () => {
  if (!shSplit) return;
  const {rows, open, out, at} = shSplit, hists = out.querySelectorAll(":scope>.share-histp");
  shSplit = null;
  for (const [b, rs] of rows) b.replaceChildren(...rs);
  open.forEach((r, i) => r.cells[0].replaceChildren(...hists[i].childNodes));
  out.remove();
  if (at !== document.activeElement && at?.isConnected) at.focus({preventScroll: true});
});

/* the sections' bar (#289): a chip per section, sticky under the page's header, that scrolls to it and gives its heading
   focus; the one in view is marked. Buttons, not #links: this page's address after the # is the link's token. On a
   narrow screen its chips scroll sideways (#491); on a wider one they wrap to two rows at most, scrolling sideways
   beyond them (#498: three or four rows were a third of a laptop's window), and under 700 px tall isn't sticky (app.css;
   #501: a phone's one row is). A chip given focus scrolls wholly into the bar's view (#501). */
function shBar(secs) {
  const bar = $("#shareBar");
  bar.replaceChildren(el("div", {class: "share-chips"}, ...secs.map(s => el("button", {class: "chip", type: "button", "data-for": "share-" + s.id, text: s.label, onclick: () => {
    const h = document.querySelector(`#share-${s.id} h2`);
    shPick = "share-" + s.id;   // marked, though the page's foot may stop it short of the bar
    h.tabIndex = -1;
    h.focus({preventScroll: true});
    h.closest("section").scrollIntoView({behavior: smoothly(), block: "start"});
    shSpy();
  }}))));
  bar.hidden = secs.length < 2;
  shTop();
}
/* the width the chips wrap in to make two rows at most (#498): the bar's own, or the least wider one that does, as the
   browser wraps them (each row as many as fit) */
function shChipsW(bar) {
  const ws = [...bar.querySelectorAll(".chip")].map(c => c.getBoundingClientRect().width), gap = parseFloat(getComputedStyle(bar.firstChild).columnGap) || 0;
  const rows = w => { let n = 1, x = 0; for (const c of ws) { if (x && x + gap + c > w) { n++; x = c; } else x += (x ? gap : 0) + c; } return n; };
  let w = bar.clientWidth;
  while (rows(w) > 2) w += 8;
  return Math.ceil(w);
}
function shTop() {   // the header's and the bar's heights: the bar sticks under the one, what gets focus scrolls clear of both
  const head = document.querySelector("body>header"), bar = $("#shareBar"), root = document.documentElement.style;
  const stuck = getComputedStyle(head).position === "sticky" ? head.offsetHeight : 0;   // not under 500 px tall (#491)
  if (bar.firstChild) root.setProperty("--chips-w", shChipsW(bar) + "px");
  root.setProperty("--head-h", head.offsetHeight + "px");
  root.setProperty("--bar-h", stuck + (bar.hidden || getComputedStyle(bar).position !== "sticky" ? 0 : bar.offsetHeight) + "px");   // the bar: not sticky under 700 px tall (#498)
}
{ const ro = new ResizeObserver(shTop); ro.observe(document.querySelector("body>header")); ro.observe($("#shareBar")); }
for (const q of ["(max-height:500px)", "(min-width:641px) and (max-height:699px)"]) matchMedia(q).addEventListener("change", shTop);
let shPick = null;   // the section a chip went to, until the page is scrolled by hand
function shSpy() {   // the section in view: of those not all under the bar (#491), the last whose top has passed under it
  const bar = $("#shareBar"), chips = [...bar.querySelectorAll(".chip")];   // or else the first; or the last, at the page's foot
  if (bar.hidden || !chips.length) return;
  const under = bar.getBoundingClientRect().bottom, line = under + 16, box = c => document.getElementById(c.dataset.for)?.getBoundingClientRect();
  const seen = chips.filter(c => box(c)?.bottom > under);
  let on = seen[0] || chips[chips.length - 1];
  for (const c of seen) if (box(c).top <= line) on = c;
  if (innerHeight + scrollY >= document.documentElement.scrollHeight - 2) on = chips[chips.length - 1];
  on = chips.find(c => c.dataset.for === shPick) || on;
  for (const c of chips) if (c === on) c.setAttribute("aria-current", "true"); else c.removeAttribute("aria-current");
  // kept in sight where the bar scrolls sideways, unless a chip has focus, which is (#501)
  if (!bar.contains(document.activeElement)) shSee(bar, on);
}
function shSee(bar, c) {   // a chip wholly in the bar's view, 8 px clear of its edges where it can be
  const r = c.getBoundingClientRect(), b = bar.getBoundingClientRect(), right = b.left + bar.clientWidth;
  if (r.left < b.left) bar.scrollLeft += r.left - b.left - 8;
  else if (r.right > right) bar.scrollLeft += Math.min(r.right - right + 8, r.left - b.left - 8);
}
$("#shareBar").addEventListener("focusin", ev => { if (ev.target.matches(".chip")) shSee(ev.currentTarget, ev.target); });
let shSpied = 0;
for (const t of ["scroll", "resize"]) addEventListener(t, () => { shSpied ||= requestAnimationFrame(() => { shSpied = 0; shSpy(); }); }, {passive: true});
for (const t of ["wheel", "touchstart", "keydown", "pointerdown"]) addEventListener(t, ev => { if (!ev.target.closest?.("#shareBar")) shPick = null; }, {passive: true});

/* a journal photo (#161), only in a link whose owner ticked "Photos in the journal": asked for by the token like the
   view itself, and shown from its own bytes as a data: address (the page's policy allows no other) */
let SH_TOKEN = "";
function shPhoto(p, fallback) {
  const img = el("img", {alt: p.alt || fallback}), fig = el("figure", {class: "share-pic"}, img, p.alt ? el("figcaption", {text: p.alt}) : null);
  (async () => {
    try {
      const r = await fetch("api/share/view", {method: "POST", cache: "no-store", credentials: "omit",
        headers: {"X-Sanguity": "1", "Content-Type": "application/json"}, body: JSON.stringify({token: SH_TOKEN, photo: p.id})});
      if (!r.ok) throw new Error(r.status);
      const blob = await r.blob();
      img.src = await new Promise((ok, no) => { const rd = new FileReader(); rd.onload = () => ok(rd.result); rd.onerror = no; rd.readAsDataURL(blob); });
    } catch (e) { fig.replaceChildren(el("p", {class: "muted", text: "This photo couldn't be loaded. Reload the page to try again."})); }
  })();
  return fig;
}

/* the phone's and watch's measures, immunizations, ECGs and BMI (#225; server/measures.py): a block's lead lines, its
   table and the note under it. Times are the local times the phone took them at, written in this browser's format.
   What its rows are comes with it, for "The latest 20 of 1,381 readings." (#527, #531: "days and readings" where they mix) */
function shBlock(b, id) {
  // the server's text as it is, its counts written as the page's own, "1,381" (#540: a typed "120,135,140" was rewritten)
  const cellOf = (kind, v) => !v ? v : kind === "day" ? shDay(v) : kind === "time" ? fmtDT(zParse(v)) : v, rows = b.rows || [];
  const cells = rs => rs.map(r => r.map((v, i) => cellOf(b.cols[i][1], v)));
  return el("div", {}, ...(b.lead || []).map(t => el("p", {text: t})),
    shLong((b.cols || []).map(c => c[0]), cells(rows), b.empty || "None in this range.", "measures", b.of, id,
      async () => cells((await shWhole(id)).rows || []), Math.max(b.total || 0, rows.length)),
    b.note ? el("p", {class: "hint", text: b.note}) : null);
}

async function shLoad() {
  const token = location.hash.slice(1);
  // what went wrong, in the header, the records of a link shown before gone (#523: its title; #527: a # changed to a
  // link that couldn't be reached threw, as the first load's #shareStatus was gone, and left them)
  const failed = (h, why) => {
    document.title = `${h} · Sanguity`;
    $("#shareHead").replaceChildren(el("div", {class: "share-hd"}, el("h1", {text: h})), el("p", {class: "hint", text: why}));
    $("#shareBody").replaceChildren();
    shBar([]);
    announce(why);   // (util.js: the page's live line)
  };
  const broken = () => failed("This link doesn't work", "It may have expired or been revoked, or the address may be incomplete. Ask the person who sent it for a new link.");
  if (!/^[A-Za-z0-9_-]{20,100}$/.test(token)) { broken(); return; }
  SH_TOKEN = token;
  SH_CUT = {};
  let r;
  try {
    r = await fetch("api/share/view", {method: "POST", cache: "no-store", credentials: "omit",
      headers: {"X-Sanguity": "1", "Content-Type": "application/json"}, body: JSON.stringify({token})});
  } catch (e) { failed("Shared records", "Couldn't reach the server. Check the connection and reload the page."); return; }
  if (r.status === 429) { failed("Shared records", "This link has been opened many times in a short while. Try again in a few minutes."); return; }
  if (!r.ok) { broken(); return; }
  const d = await r.json();
  SH_CUT = d.cut || {};   // the long sections, sent only their latest rows (#498)
  const range = d.from && d.to ? `from ${shDay(d.from)} to ${shDay(d.to)}` : d.from ? `from ${shDay(d.from)} on` : d.to ? `up to ${shDay(d.to)}` : "of any date";
  document.title = `${d.profile} · shared records · Sanguity`;
  $("#shareHead").replaceChildren(el("div", {class: "share-hd"}, el("h1", {text: `${d.profile}: shared records`}),
    el("button", {class: "btn", type: "button", text: "Print", onclick: () => print()})),
    el("p", {class: "hint"}, keepDates(`Records ${range}, shared read-only through a link by an owner of this profile. ` +
      `The link stops working on ${fmtDT(d.expires * 1000)}, or earlier if it's revoked. Each time it's opened, the profile's owners see that in its activity log.`)));
  // #161: the journal's photos are shown with their entries, not as a section of their own
  const secs = d.sections.filter(s => s.id !== "journal_photos");
  $("#shareBody").replaceChildren(...secs.map(s => el("section", {class: "card", id: "share-" + s.id},
    el("h2", {text: s.label}), el("p", {class: "hint", text: "Shows " + s.what + "."}),
    SH_DRAW[s.id] ? SH_DRAW[s.id](d[s.id] || []) : shBlock(d[s.id] || {}, s.id))));   // a measure's block (#225)
  shBar(secs);
  shSpy();
}
shLoad();
addEventListener("hashchange", shLoad);
