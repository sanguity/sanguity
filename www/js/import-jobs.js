/* Sanguity · importing an upload in the background (#20). The import page (#19) calls
     window.sanguityImport.uploaded({profileId, uploadId[, container][, importer]})
   once an upload is finished. This starts the import, polls it (about once a second, slower the longer it runs),
   shows the stage, then its review (#22, #282: jobReview): what it would add by kind, the dates it covers, and the
   duplicates of what the profile has, skipped unless ticked to keep, lab names Sanguity doesn't know with a series
   suggested for each (#23: confirm it, pick another or keep it separate; remembered for later imports), results
   possibly the same as one another source brought (#96: ticked to show once), with its Add button and Cancel. Adding
   polls again and shows what came in, with a link to the timeline; cancelling throws the import and its upload away.
   Plain script: no inline code (the pages' CSP allows scripts from js/ only); from other files it needs util.js (el,
   icon, kindIcon, CAT_OF, keepDates, aboutWords for the ⓘ, #280) and dates.js. */
"use strict";
(function () {
  const STAGES = {queued: "Waiting for another import to finish…", detecting: "Recognising the files…",
                  parsing: "Reading the records…", saving: "Saving the records…"};
  const KIND = {visit: ["visit", "visits"], lab: ["lab draw", "lab draws"], imaging: ["imaging study", "imaging studies"],
                procedure: ["procedure", "procedures"], study: ["report", "reports"], message: ["message", "messages"],
                appt_msg: ["appointment message", "appointment messages"], auto_msg: ["automated message", "automated messages"],
                letter: ["letter", "letters"], document: ["document", "documents"], stay: ["hospital stay", "hospital stays"],
                med: ["medication", "medications"], rx: ["pharmacy fill", "pharmacy fills"], claim: ["claim", "claims"],
                exercise: ["workout", "workouts"], immunization: ["immunization", "immunizations"]};   // #179

  function node(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "text") e.textContent = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
    }
    for (const c of kids.flat()) if (c != null) e.append(c);
    return e;
  }
  // the background behind an ⓘ at the end of a part's instructions (#280, util.js aboutWords), named for what it's about
  const about = (title, text) => aboutWords({title}, text);
  function call(path, opts) {
    opts = opts || {};
    const write = opts.method && opts.method !== "GET";
    return fetch("api/" + path, {cache: "no-store", credentials: "same-origin", ...opts,
      headers: write ? {"X-Sanguity": "1", "Content-Type": "application/json"} : {}});
  }
  async function detail(r) {
    const j = await r.json().catch(() => ({}));
    return typeof j.detail === "string" ? j.detail : `The server answered ${r.status}.`;
  }
  const plural = (n, [one, many]) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
  const day = s => { const t = dayTs(s); return isNaN(t) ? s : fmtD(t); };   // dates.js

  function box(container) {
    let host = container;
    if (typeof host === "string") host = document.querySelector(host);
    if (!host) host = document.querySelector("#import-status");
    if (!host) { host = node("section", {id: "import-status"}); (document.querySelector("main") || document.body).append(host); }
    const card = node("div", {class: "card import-job", role: "status", "aria-live": "polite"});
    host.append(card);
    return card;
  }

  function countItems(c) {
    const kinds = Object.entries(c.kinds || {}).sort((a, b) => b[1] - a[1]);
    const items = kinds.filter(([k]) => k !== "labitem").map(([k, n]) => node("li", {text: plural(n, KIND[k] || [k, k])}));
    for (const [k, words] of EXTRA) {   // the rest, in the review's order (below)
      if (c[k] && !(k === "workouts" && (c.kinds || {}).exercise))
        items.push(node("li", {text: plural(c[k], words) + (k === "results" ? " in " + plural(c.series || 0, ["test", "tests"]) : "")}));
    }
    return items;
  }
  const rangeText = r => r ? (r.from === r.to ? ` from ${day(r.from)}` : ` from ${day(r.from)} to ${day(r.to)}`) : "";
  const notes = j => (j.warnings || []).length ? node("details", {}, node("summary", {text: plural(j.warnings.length, ["note", "notes"]) + " from the import"}),
                                                 node("ul", {}, j.warnings.map(w => node("li", {text: w})))) : null;

  /* ---- the review (#282): what an import ready to review would add, and the choices to make before it's saved.
     jobReview(job, card) fills the card (the card's frame, its stage line and focus are the job's, above) and resolves
     with the job once it's saved (or failed) or cancelled. In it: the format and the dates; a tile per kind of record
     in the Timeline's colours with a few of each; how many are new, already here or possibly the same; the lab names
     to match, a table whose series are typed with a list to pick from; results possibly the same as another source's
     and the duplicates, each acted on only when ticked; and a bar at the foot whose button says what it adds. */
  const KEEP = "";   // a lab name kept as a series of its own: no series has an empty name
  const unitText = u => u ? ` (${u})` : "";

  // the lab names to match (#23), one row each: the series typed or picked from a list (the match made before or the
  // suggestion first), or left empty to keep it a series of its own. -> {part, picked, wrong}
  function labTable(j) {
    const listed = j.labs || [];
    if (!listed.length) return {part: null, picked: () => ({}), wrong: () => null};
    const named = new Map((j.lab_targets || []).map(t => [t.name.toLowerCase(), t.name]));
    const rows = listed.map((l, i) => {
      const id = `imp${j.id}-lab${i}`, before = l.before;
      const sure = (l.suggestions || []).filter(s => s.sure), unsure = (l.suggestions || []).filter(s => !s.sure);
      const opts = [], seen = new Set();
      const add = (value, label) => { if (seen.has(value)) return; seen.add(value); named.set(value.toLowerCase(), value); opts.push(node("option", {value, label})); };
      if (before && before.target) add(before.target, "Matched as before");
      for (const s of [...sure, ...unsure]) add(s.name, `${s.sure ? "Suggested" : "Possibly"}: ${s.reason}`);
      for (const t of j.lab_targets || []) add(t.name, t.unit || null);
      const initial = before ? before.target || KEEP : sure.length ? sure[0].name : KEEP;
      const top = before ? null : sure[0] || unsure[0];
      const said = before ? (before.target == null ? "Kept separate as before." : "Matched as before.")
                 : top ? `${top.sure ? "Suggested" : "Possibly"}: ${top.name}, ${top.reason}.` : "Nothing in this profile looks like it.";
      const why = node("span", {class: before ? "before" : "why", id: id + "-why", text: said});
      const box = node("input", {type: "text", list: id + "-list", value: initial, placeholder: "A series of its own", autocomplete: "off",
                                 spellcheck: "false", "aria-label": `Series for ${l.name}`, "aria-describedby": id + "-why", "data-ref": l.ref});
      // what's typed, as a series' own name (any case), "" to keep it separate, or null when there's no such series
      const read = () => { const v = box.value.trim(); return v ? named.get(v.toLowerCase()) ?? null : KEEP; };
      box.addEventListener("input", () => {
        if (read() !== null && box.hasAttribute("aria-invalid")) { box.removeAttribute("aria-invalid"); why.textContent = said; why.classList.remove("err"); }
      });
      box.addEventListener("change", () => {
        const bad = read() === null;
        if (bad) box.setAttribute("aria-invalid", "true"); else box.removeAttribute("aria-invalid");
        why.classList.toggle("err", bad);
        why.textContent = bad ? "No series by that name: pick one from the list, or leave it empty to keep it separate." : said;
      });
      const tr = node("tr", {"data-ref": l.ref},
        node("td", {}, node("b", {text: l.name}), unitText(l.unit), l.loinc ? node("span", {class: "loinc", text: ` · LOINC ${l.loinc}`}) : null),
        node("td", {class: "num", "data-label": "Results"}, (l.n || 0).toLocaleString()),
        node("td", {}, box, node("datalist", {id: id + "-list"}, opts), why));
      return {l, box, read, initial, tr};
    });
    const part = node("details", {class: "imp-labs", open: ""},
      node("summary", {text: `${plural(listed.length, ["lab name", "lab names"])} to match`}),
      node("p", {class: "howto"}, "Sanguity doesn't know these test names. Type or pick the series each belongs to, or leave it empty to keep it " +
        "separate; your choice is used for later imports from this source, on every profile of this server. ",
        about("matching lab names", "In a series, a result is charted with the same test from other labs. Only the series changes: each result keeps the " +
        "name the lab gave it. A match made before can be changed here for this import; to change it for good, ask an admin (Admin, Lab name matches).")),
      node("table", {class: "data stack"},
        node("thead", {}, node("tr", {}, node("th", {scope: "col", text: "Name from the lab"}), node("th", {scope: "col", class: "num", text: "Results"}),
                                         node("th", {scope: "col", text: "Series"}))),
        node("tbody", {}, rows.map(r => r.tr))));
    // sent, and remembered for later imports: a suggestion left chosen, or any choice changed by hand. A name matched
    // before is sent only when changed (for this import only); "keep it separate" left as it was isn't sent
    const picked = () => Object.fromEntries(rows.map(r => [r, r.read()]).filter(([r, v]) => v !== r.initial || (!r.l.before && r.initial !== KEEP))
      .map(([r, v]) => [r.l.ref, v === KEEP ? null : v]));
    // the first box naming no series, marked as such
    const wrong = () => { const r = rows.find(r => r.read() === null); if (r) r.box.dispatchEvent(new Event("change")); return r ? r.box : null; };
    return {part, picked, wrong};
  }

  // results that look like ones already in the profile from another source (#96): flagged, never merged unless ticked
  const when = t => { const d = new Date(String(t).length === 10 ? t + "T12:00:00" : t); if (isNaN(d)) return String(t); const o = {year: "numeric", month: "short", day: "numeric"};
    return /T(00:00|12:00):00$/.test(t) || String(t).length === 10 ? d.toLocaleDateString(undefined, o) : d.toLocaleString(undefined, {...o, hour: "numeric", minute: "2-digit"}); };
  const valueText = x => `${x.value} ${x.unit || ""}`.trim();
  function sameChoices(j) {
    const listed = j.possible || [];
    if (!listed.length) return {part: null, picked: () => []};
    const boxes = [];
    const part = node("details", {class: "imp-same", open: ""},
      node("summary", {text: `${plural(listed.length, ["result", "results"])} possibly in the profile already, from another source`}),
      node("p", {class: "howto"}, "Each of these has the same test, time and value as a result another source brought. " +
        "Tick the ones that are the same result to show them once; nothing is merged unless you tick it. ",
        about("results shown once", "Values are compared with their units converted. A result shown once lists both sources, and you can undo the link " +
        "later from the result's panel.")),
      node("ul", {}, listed.map(x => {
        const b = node("input", {type: "checkbox", value: x.ref, "aria-label": `Same result: ${x.name} ${valueText(x)}`});
        boxes.push(b);
        return node("li", {"data-ref": x.ref}, node("label", {}, b, " Same result"),
          node("span", {}, node("b", {text: x.name}), ` ${valueText(x)} · ${when(x.t)} · from ${j.label || j.importer}`),
          node("span", {class: "why", text: `Possibly the same result as ${x.other.name} ${valueText(x.other)} · ${when(x.other.t)} · from ${x.other.label || "another import"}`}));
      })));
    return {part, picked: () => boxes.filter(b => b.checked).map(b => b.value)};
  }

  // the duplicates of what the profile has (#22): skipped, each with a box to add it again anyway
  function dupChoices(j) {
    const d = j.duplicates || {items: []}, n = (d.events || 0) + (d.results || 0), boxes = [];
    const part = d.items && d.items.length ? node("details", {class: "imp-dups"},
      node("summary", {text: `Already here: ${plural(n, ["duplicate", "duplicates"])}, skipped unless ticked`}),
      node("p", {class: "hint", text: "These are in the profile already. Tick any you want added again anyway."}),
      node("ul", {}, d.items.map(it => {
        const b = node("input", {type: "checkbox", value: it.ref});
        boxes.push(b);
        const what = it.kind === "results" ? plural(it.n || 0, ["lab result", "lab results"]) + ": " + it.title
                                           : (KIND[it.kind] || [it.kind])[0] + ": " + it.title;
        return node("li", {}, node("label", {}, b, " ", node("span", {class: "d", text: it.t ? day(it.t) : ""}), " ", what));
      })),
      d.more ? node("p", {class: "hint", text: `…and ${d.more.toLocaleString()} more.`}) : null)
      : n ? node("p", {class: "hint", text: `Already here: ${plural(n, ["duplicate", "duplicates"])}, skipped.`}) : null;
    // what the ticked ones add, by count key: a result's n results, an event one of its kind
    const ticked = () => (d.items || []).filter((it, i) => boxes[i] && boxes[i].checked);
    return {part, picked: () => ticked().map(it => it.ref), ticked};
  }

  // what an import brings besides events, in the order the counts are listed: [count key, words, icon, colour]
  // (#179 the profile's lists, #182 ECGs, #223 an Apple Health export's readings and cycle records)
  const EXTRA = [["results", ["lab result", "lab results"], "test-tube-diagonal", "var(--c-lab)"],
    ["documents", ["attached file", "attached files"], "file-text", "var(--c-letter)"],
    ["allergies", ["allergy", "allergies"], "triangle-alert", "var(--muted)"],
    ["problems", ["problem or diagnosis", "problems or diagnoses"], "clipboard", "var(--c-contact)"],
    ["upcoming", ["upcoming appointment", "upcoming appointments"], "calendar", "var(--c-visit)"],
    ["watch_days", ["day of activity data", "days of activity data"], "watch", "var(--ink2)"],
    ["workouts", KIND.exercise, "dumbbell", "var(--ink2)"],
    ["ecgs", ["ECG recording", "ECG recordings"], "activity", "var(--c-ecg)"],
    ["rhythm", ["irregular rhythm notification", "irregular rhythm notifications"], "activity", "var(--c-ecg)"],
    ["bp_readings", ["blood pressure reading", "blood pressure readings"], "heart-pulse", "var(--ink2)"],
    ["temp_readings", ["body temperature reading", "body temperature readings"], "gauge", "var(--ink2)"],
    ["glucose_readings", ["glucose meter reading", "glucose meter readings"], "droplet", "var(--c-journal)"],
    ["cgm_days", ["day of CGM readings", "days of CGM readings"], "droplet", "var(--c-journal)"],
    ["heights", ["height, offered for About this person", "heights, offered for About this person"], "ruler", "var(--ink2)"],
    ["cycle", ["cycle tracking record", "cycle tracking records"], "droplet", "var(--c-cycle)"]];
  const RECORDS = new Set(["results", "allergies", "problems", "upcoming"]);   // besides events: what's a record
  // the counts c, each {k, n, words, mark, tone, record}: events by kind, most first (a lab draw left out where there are
  // results: they're its), then EXTRA's
  function counted(c) {
    const kinds = c.kinds || {};
    const ev = Object.entries(kinds).filter(([k, n]) => n > 0 && k !== "labitem" && !(k === "lab" && c.results)).sort((a, b) => b[1] - a[1])
      .map(([k, n]) => ({k, n, words: KIND[k] || [k, k], mark: () => kindIcon(k), tone: (CAT_OF[k] || {}).color || "var(--muted)", record: true}));
    return ev.concat(EXTRA.filter(([k]) => c[k] > 0 && !(k === "workouts" && kinds.exercise))
      .map(([k, words, ic, tone]) => ({k, n: c[k], words, mark: () => icon(ic), tone, record: RECORDS.has(k)})));
  }
  const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
  // a kind's icon in its colour, by its colour attribute (SVG's presentation of color: the CSS's currentColor, .imp-k)
  const toned = (i, c) => { i.classList.add("imp-k"); i.setAttribute("color", c); return i; };

  // a tile per kind, with a few of what's new of it (the job's samples, latest first)
  function tiles(j) {
    const c = j.counts || {}, s = j.samples || {}, list = counted(c);
    if (!list.length) return null;
    const rows = k => k === "results" ? (s.results || []).map(r => [r.t, `${r.name} ${valueText(r)}`])
      : (k in (s.events || {}) ? s.events[k] : s[k] || []).map(r => [r.t, r.title]);
    return node("div", {class: "tiles imp-tiles"}, list.map(x => {
      const some = rows(x.k).filter(r => r[1]);
      return node("div", {class: "tile"},
        node("span", {class: "lbl"}, toned(x.mark(), x.tone), cap(x.words[x.n === 1 ? 0 : 1])),
        node("span", {class: "val", text: x.n.toLocaleString()}),
        x.k === "results" ? node("span", {class: "delta", text: "in " + plural(c.series || 0, ["test", "tests"]) +
          ((c.kinds || {}).lab ? ", from " + plural(c.kinds.lab, KIND.lab) : "")}) : null,
        some.length ? node("ul", {}, some.map(([t, what]) => node("li", {}, t ? node("span", {class: "d", text: day(t)}) : null, " ", what)),
                           x.n > some.length ? node("li", {class: "more", text: `and ${(x.n - some.length).toLocaleString()} more`}) : null) : null);
    }));
  }

  // how many records are new, already here or possibly the same as another source's: a bar in three parts and its key
  function split(j) {
    const c = j.counts || {}, d = j.duplicates || {}, same = (j.possible || []).length, labs = (j.total || c).results > 0;
    const records = counted(c).filter(x => x.record).reduce((a, x) => a + x.n, 0);
    const here = (d.events || 0) - (labs ? (d.kinds || {}).lab || 0 : 0) + (d.results || 0) + (d.other || 0);
    const parts = [["new", Math.max(0, records - same), "new"], ["here", Math.max(0, here), "already here"], ["same", same, "possibly the same"]];
    const total = parts.reduce((a, p) => a + p[1], 0);
    if (!total) return null;
    let x = 0;
    const bar = el("svg:svg", {class: "imp-bar", viewBox: `0 0 ${total} 1`, preserveAspectRatio: "none", "aria-hidden": "true", focusable: "false"},
      parts.filter(p => p[1]).map(([k, n]) => { const r = el("svg:rect", {class: k, x, y: 0, width: n, height: 1}); x += n; return r; }));
    return node("div", {class: "imp-split"}, bar,
      node("ul", {}, parts.map(([k, n, words]) => node("li", {class: k}, node("b", {text: n.toLocaleString()}), " " + words))));
  }

  // the button's words: what it adds (#282), "Add 6 lab results", or null when it adds nothing
  function addWords(c) {
    const list = counted(c).filter(x => x.k !== "documents");
    if (!list.length) return null;
    if (list.length === 1) return "Add " + plural(list[0].n, list[0].words);
    const records = list.filter(x => x.record);
    return records.length === list.length ? "Add " + plural(records.reduce((a, x) => a + x.n, 0), ["record", "records"]) : "Add all of this";
  }

  function jobReview(job, card) {
    const j = job, pid = j.profile_id, base = `profiles/${encodeURIComponent(pid)}/jobs/${j.id}`;
    return new Promise(done => {
      const range = j.range ? (j.range.from === j.range.to ? day(j.range.from) : `${day(j.range.from)} – ${day(j.range.to)}`) : "";
      const labs = labTable(j), same = sameChoices(j), dups = dupChoices(j);
      const msg = node("span", {class: "msg", role: "status"});
      const add = node("button", {class: "btn primary", type: "button"});
      const cancel = node("button", {class: "btn", type: "button", text: "Cancel"});
      // the button follows the duplicates ticked: each adds its results, or one of its kind
      const words = () => {
        const c = JSON.parse(JSON.stringify(j.counts || {}));
        c.kinds = c.kinds || {};
        for (const it of dups.ticked()) {
          if (it.kind === "results") c.results = (c.results || 0) + (it.n || 0);
          else c.kinds[it.kind] = (c.kinds[it.kind] || 0) + 1;
        }
        const w = addWords(c);
        add.textContent = w || "Nothing to add";
        add.disabled = !w;
      };
      words();
      if (dups.part) dups.part.addEventListener("change", words);
      const say = t => { msg.textContent = t; };
      const busy = on => { cancel.disabled = on; if (on) add.disabled = true; else words(); };
      const act = async (what, body) => {
        let r;
        try { r = await call(`${base}/${what}`, {method: "POST", body: JSON.stringify(body || {})}); }
        catch (e) { say("Couldn't reach the server. Check the connection and try again."); return false; }
        if (r.status === 401) { location.replace("login.html"); return false; }
        if (!r.ok) { say(await detail(r)); return false; }
        return true;
      };
      add.addEventListener("click", async () => {
        const bad = labs.wrong();
        if (bad) { say("A lab name has a series that isn't there: pick one from its list, or leave it empty."); bad.focus(); return; }
        busy(true); say("Adding…");
        if (!(await act("confirm", {keep: dups.picked(), labs: labs.picked(), same: same.picked()}))) { busy(false); return; }
        const status = node("p", {text: STAGES.saving});
        card.replaceChildren(status);
        done(poll(card, pid, j.id, status));
      });
      cancel.addEventListener("click", async () => {
        busy(true); say("Cancelling…");
        if (!(await act("discard"))) { busy(false); return; }
        showCancelled(card);
        done(j);
      });
      fill(card,
        node("p", {class: "imp-state", text: j.new ? "Ready to import" : "All duplicates"}),
        node("h3", {class: "imp-title"}, keepDates([j.label || j.importer, range].filter(Boolean).join(" · "))),
        node("p", {class: "hint", text: j.new ? "Nothing is saved until you add it with the button at the foot."
          : "Everything in this upload is in this profile already. Tick a duplicate to add it again anyway, or cancel."}),
        j.new ? tiles(j) : null, split(j), labs.part, same.part, dups.part, notes(j),
        node("div", {class: "sg-bar imp-actions", role: "region", "aria-label": "Add or cancel this import"},
          node("span", {class: "sg-bar-what"}, node("span", {class: "sg-bar-n", text: "Not saved yet"}), msg),
          node("span", {class: "sg-bar-acts"}, cancel, add)));
    });
  }

  // replaceChildren turns a null into the text "null"; parts that aren't there are left out instead
  function fill(card, ...parts) { card.replaceChildren(...parts.filter(p => p != null)); }
  /* a card whose import is over (saved, failed or cancelled): Sources clears it once another section is shown (#283) */
  const settled = card => { card.dataset.settled = ""; };

  function showDone(card, pid, j) {
    settled(card);
    const c = j.counts || {}, items = countItems(c), sk = j.skipped || {};
    const skipped = (sk.events || 0) + (sk.results || 0), ln = j.lab_names || {};
    fill(card,
      node("p", {}, node("b", {text: "Imported."}), ` Read as ${j.label || j.importer}${rangeText(j.range)}.`),
      items.length ? node("ul", {}, items) : node("p", {text: skipped ? "Nothing new: it was all in the profile already." : "The files held no records to show."}),
      skipped ? node("p", {class: "hint", text: `${plural(skipped, ["duplicate", "duplicates"])} skipped.`}) : null,
      ln.matched ? node("p", {class: "hint", text: `${plural(ln.matched, ["lab name", "lab names"])} put in an existing series.`}) : null,
      j.linked ? node("p", {class: "hint", text: `${plural(j.linked, ["result", "results"])} shown once, as the same result from another source.`}) : null,
      notes(j),
      node("p", {}, node("a", {href: `index.html?profile=${pid}#timeline`, text: "See them on the timeline"})));   // its profile and tab (#208)
    // said by a toast (#263), with the way to what came in and an Undo, which asks first in Import history's words
    // (#360: it can't be reversed): the card stays as it is, no longer said itself
    card.removeAttribute("role"); card.removeAttribute("aria-live");
    const what = items.map(li => li.textContent);
    const labs = c.results > 0, where = labs ? "labs" : "timeline";
    toast(what.length ? `Imported ${what.slice(0, 2).join(", ")}${what.length > 2 ? " and more" : ""}.` : "Imported: nothing new.", {
      actions: [{text: labs ? "Open Labs" : "Open the timeline", href: `index.html?profile=${pid}#${where}`},
        ...(j.import_id != null && what.length ? [{text: "Undo…", onClick: () => undoImport(card, pid, j, what)}] : [])]});
  }
  /* the Undo of a toast: Import history's, asked first as it asks (false: kept), the card then saying it's undone */
  async function undoImport(card, pid, j, what) {
    if (!await ask({title: "Undo this import?", danger: true, action: "Undo import", cancel: "Keep it",
                    message: `Remove everything this ${j.label || j.importer} import brought: ${what.join(", ").toLowerCase()}, and the files uploaded for it. This can't be reversed; you can import the files again.`})) return false;
    const id = j.import_id;
    const r = await api(`profiles/${encodeURIComponent(pid)}/imports/${id}/undo`, {method: "POST"});
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(typeof e.detail === "string" ? e.detail : `the server answered ${r.status}`); }
    card.replaceChildren(node("p", {}, node("b", {text: "Undone."}), " The import's records are gone from the profile; Import history lists it as undone."));
    toast("Undone: the import's records are gone from the profile.");
  }

  function showFailed(card, text) {
    // the message is plain text; any address in it (the issues page) becomes a link, nothing else is markup
    const parts = String(text || "The import failed; nothing was saved.").split(/(https:\/\/[^\s)]+[^\s).,])/);
    card.replaceChildren(node("p", {}, node("b", {text: "Not imported. "}),
      parts.map((p, i) => i % 2 ? node("a", {href: p, target: "_blank", rel: "noopener noreferrer", text: p}) : p)));
    card.classList.add("error");
    settled(card);
  }

  async function uploaded(opts) {
    const pid = opts && opts.profileId, up = opts && opts.uploadId;
    const card = box(opts && opts.container);
    const status = node("p", {text: "Starting the import…"});
    card.replaceChildren(status);
    let r;
    try {
      r = await call(`profiles/${encodeURIComponent(pid)}/uploads/${encodeURIComponent(up)}/import`,
                     {method: "POST", body: JSON.stringify(opts && opts.importer ? {importer: opts.importer} : {})});
    } catch (e) { showFailed(card, "Couldn't reach the server to start the import. Check the connection and try again."); return null; }
    if (r.status === 401) { location.replace("login.html"); return null; }
    if (!r.ok) { showFailed(card, await detail(r)); return null; }
    return poll(card, pid, (await r.json()).id, status);
  }

  async function poll(card, pid, jid, status) {
    card.dataset.job = jid;   // which import it is: Sources' "Waiting for you" opens the card there is, not another (#283)
    const began = Date.now();
    let wait = 1000, misses = 0;
    for (;;) {
      await new Promise(go => setTimeout(go, wait));
      let j = null;
      try {
        const g = await call(`profiles/${encodeURIComponent(pid)}/jobs/${jid}`);
        if (g.status === 401) { location.replace("login.html"); return null; }
        if (!g.ok) { showFailed(card, await detail(g)); return null; }
        j = await g.json(); misses = 0;
      } catch (e) {
        misses++;
        status.textContent = "Lost touch with the server; still trying… (the import carries on there)";
      }
      if (j) {
        if (j.state === "done") { showDone(card, pid, j); return j; }
        if (j.state === "failed") { showFailed(card, j.message); return j; }
        if (j.state === "discarded") { showCancelled(card); return j; }
        if (j.state === "preview") return jobReview({profile_id: pid, ...j}, card);
        const secs = Math.round((Date.now() - began) / 1000);
        const stage = j.state === "queued" && j.stage !== "saving" ? "queued" : j.stage;
        status.textContent = (STAGES[stage] || "Working…") + (secs >= 5 ? ` (${secs} s)` : "");
      }
      // about once a second at first; slower for a long import, and slower still while the server can't be reached
      const elapsed = Date.now() - began;
      wait = misses ? Math.min(10000, 1000 * 2 ** misses) : elapsed < 20000 ? 1000 : elapsed < 120000 ? 2000 : 5000;
    }
  }

  function showCancelled(card) {
    settled(card);
    card.replaceChildren(node("p", {}, node("b", {text: "Cancelled."}), " Nothing was saved, and the upload was removed."));
  }

  // an import someone else started (a connected source's first pull, connect.js), or one left at its preview (Sources'
  // "Waiting for you", #283, with opts.text: what it says until the preview is there): its progress and preview here
  function follow(opts) {
    const card = box(opts && opts.container), status = node("p", {text: opts.text || "Reading the records…"});
    card.replaceChildren(status);
    return poll(card, opts.profileId, opts.jobId, status);
  }

  window.sanguityImport = {uploaded, follow};
})();
