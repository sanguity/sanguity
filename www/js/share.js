/* Sanguity · a share link's page (#47): the part of a profile its owner chose to show, read-only, for someone
   without an account (server/share_links.py).

   The token is in this page's address after the #, which the browser never sends to any server: it's posted to
   api/share/view in the request body. The answer holds only what the link shows; nothing here can change anything,
   sign anyone in or reach any other page's data. No cookie is used or set, and nothing is kept in this browser.
   Dates are shown in this browser's own zone and format (dates.js, without anyone's settings). */
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

/* a table that scrolls sideways inside its card on a narrow screen, never the page */
function shTable(heads, rows, empty, cls = "") {   // cls "measures": stays a table on a phone (#225)
  if (!rows.length) return el("p", {class: "muted", text: empty});
  return el("div", {class: "share-scroll", tabindex: 0, role: "region", "aria-label": heads.filter(Boolean).join(", ")}, el("table", {class: ("tbl " + cls).trim()},
    el("thead", {}, el("tr", {}, ...heads.map(h => el("th", {text: h})))),
    el("tbody", {}, ...rows.map(r => el("tr", {}, ...r.map(c => c && c.nodeType ? el("td", {}, c) : el("td", {text: c == null ? "" : String(c)})))))));
}

const SH_DRAW = {
  visits: list => shTable(["Date", "What", "Details"], list.map(v => [v.end ? `${shDay(v.t)} – ${shDay(v.end)}` : shWhen(v.t),
    (SH_KIND[v.kind] && v.kind !== "visit" ? SH_KIND[v.kind] + ": " : "") + v.title, v.sub]), "No visits or stays in this range."),
  procedures: list => shTable(["Date", "Kind", "Title"], list.map(v => [shDay(v.t), SH_KIND[v.kind] || "", v.title]), "None in this range."),
  medications: list => shTable(["Medication", "Instructions", "From", "Until"], list.map(m => [m.name + (m.status ? ` (${m.status})` : ""),
    m.instructions, shDay(m.since), m.until ? shDay(m.until) : "—"]), "No medications in this range."),
  results: list => list.length ? el("div", {}, ...list.map(s => el("div", {class: "share-lab"},
    el("h3", {class: "sub", text: s.name + (s.unit ? ` (${s.unit})` : "")}),
    shTable(["Date", "Result", "Reference range", ""], s.results.map(p => [shWhen(p.t), p.value, p.range,
      p.flag ? el("span", {class: "share-flag", text: p.flag}) : ""]), "")))) : el("p", {class: "muted", text: "No lab results in this range."}),
  appointments: list => shTable(["When", "What", "With", "Where"], list.map(a => [shWhen(a.t), a.title, a.who, a.where]), "No upcoming appointments."),
  journal: list => list.length ? el("div", {}, ...list.map(j => el("div", {class: "share-entry"},
    el("div", {}, el("b", {text: (SH_KIND[j.kind] || "Note") + (j.title ? `: ${j.title}` : "")}),
      j.severity ? el("span", {class: "tag", text: `severity ${j.severity}`}) : null,
      j.kind === "mood" ? el("span", {class: "tag", text: [j.mood != null && `mood ${j.mood}/10`, j.energy != null && `energy ${j.energy}/10`].filter(Boolean).join(", ")}) : null,
      el("span", {class: "muted", text: " " + shWhen(j.t)})),
    j.where?.length || j.group ? el("p", {class: "muted", text: [j.where?.length && "Where: " + j.where.join(", "), j.group && `Logged with “${j.group}”`].filter(Boolean).join(" · ")}) : null,   // #161
    j.note ? el("p", {class: "share-note", text: j.note}) : null,
    j.photos?.length ? el("div", {class: "share-pics"}, ...j.photos.map((p, i) => shPhoto(p, `Photo ${i + 1} of ${j.photos.length} for ${j.title || "this entry"}`))) : null)))
    : el("p", {class: "muted", text: "No journal entries in this range."}),
  about: a => el("div", {}, el("dl", {class: "share-about"},   // #194: only when the link's owner ticked it
    el("dt", {text: "Birth date"}), el("dd", {text: a.birthDate ? shDay(a.birthDate) : "Not given"}),
    el("dt", {text: "Sex used for reference ranges"}), el("dd", {text: a.sex || "Not given"})),
    shTable(["Date", "Height"], (a.heights || []).map(h => [shDay(h.date), h.text]), "No heights written down in this range.")),
  costs: list => shTable(["Date", "Service", "Provider", "Billed", "Insurance paid", "Your share"],
    list.map(c => [shDay(c.t), c.title, c.provider, shUsd(c.billed), shUsd(c.paid), shUsd(c.owe)]), "No claims in this range."),
};

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
   table and the note under it. Times are the local times the phone took them at, written in this browser's format. */
function shBlock(b) {
  const cellOf = (kind, v) => !v ? v : kind === "day" ? shDay(v) : kind === "time" ? fmtDT(zParse(v)) : v;
  return el("div", {}, ...(b.lead || []).map(t => el("p", {text: t})),
    shTable((b.cols || []).map(c => c[0]), (b.rows || []).map(r => r.map((v, i) => cellOf(b.cols[i][1], v))), b.empty || "None in this range.", "measures"),
    b.total > (b.rows || []).length ? el("p", {class: "hint", text: `The latest ${b.rows.length} of ${b.total}.`}) : null,
    b.note ? el("p", {class: "hint", text: b.note}) : null);
}

async function shLoad() {
  const status = $("#shareStatus");
  const token = location.hash.slice(1);
  const broken = () => {
    $("#shareHead").replaceChildren(el("h2", {text: "This link doesn't work"}),
      el("p", {class: "hint", text: "It may have expired or been revoked, or the address may be incomplete. Ask the person who sent it for a new link."}));
    $("#shareBody").replaceChildren();
  };
  if (!/^[A-Za-z0-9_-]{20,100}$/.test(token)) { broken(); return; }
  SH_TOKEN = token;
  let r;
  try {
    r = await fetch("api/share/view", {method: "POST", cache: "no-store", credentials: "omit",
      headers: {"X-Sanguity": "1", "Content-Type": "application/json"}, body: JSON.stringify({token})});
  } catch (e) { status.textContent = "Couldn't reach the server. Check the connection and reload the page."; return; }
  if (r.status === 429) { status.textContent = "This link has been opened many times in a short while. Try again in a few minutes."; return; }
  if (!r.ok) { broken(); return; }
  const d = await r.json();
  const range = d.from && d.to ? `from ${shDay(d.from)} to ${shDay(d.to)}` : d.from ? `from ${shDay(d.from)} on` : d.to ? `up to ${shDay(d.to)}` : "of any date";
  document.title = `${d.profile} · shared records · Sanguity`;
  $("#shareHead").replaceChildren(el("h2", {text: `${d.profile}: shared records`}),
    el("p", {class: "hint", text: `Shared read-only through a link, by an owner of this profile. It shows records ${range}, ` +
      `in these sections only: ${d.sections.map(s => s.label).join(", ")}. The link stops working on ${fmtDT(d.expires * 1000)}, ` +
      "or earlier if it's revoked. Each time it's opened, the profile's owners see that in its activity log."}));
  // #161: the journal's photos are shown with their entries, not as a section of their own
  $("#shareBody").replaceChildren(...d.sections.filter(s => s.id !== "journal_photos").map(s => el("section", {class: "card", id: "share-" + s.id},
    el("h2", {text: s.label}), el("p", {class: "hint", text: "Shows " + s.what + "."}),
    SH_DRAW[s.id] ? SH_DRAW[s.id](d[s.id] || []) : shBlock(d[s.id] || {}))));   // a measure's block (#225)
}
shLoad();
addEventListener("hashchange", shLoad);
