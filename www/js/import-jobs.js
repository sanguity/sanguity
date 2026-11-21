/* Sanguity · importing an upload in the background (#20), in one card from the first file sent to the records saved
   (#281). The upload's drop target (import.js) makes the card (frame, below), sends the files with it, then calls
     window.sanguityImport.uploaded({profileId, uploadId[, frame][, container][, importer][, title]})
   once the upload is finished; a Health Link (import-shl.js) calls it too, and follow() shows an import started
   elsewhere (a connected source's first pull, one waiting for you). This starts the import, polls it (about once a
   second, slower the longer it runs), shows the stage, then its review (#22, #282: jobReview): what it would add by kind, the dates it covers, and the
   duplicates of what the profile has, skipped unless ticked to keep, lab names Sanguity doesn't know with a series
   suggested for each (#23: confirm it, pick another or keep it separate; remembered for later imports), results
   possibly the same as one another source brought (#96: ticked to show once), with its Add button and Cancel. Adding
   polls again and shows what came in, with a link to the timeline; cancelling throws the import and its upload away.

   The card (#281): what's imported, the stage line (Sending → Unpacking → Recognising → Review → Saved; an import that
   wasn't uploaded here starts at Recognising), one progress bar, a line saying what's happening now (said as each
   stage starts), then the job's own part (.import-job), which the review, the result or what went wrong fills. Focus
   moves to the review once it's ready, unless it has gone somewhere else on the page meanwhile; after its Add or
   Cancel, to the line saying how it went (#489). fresh(card) draws a review again, as the server reads it now (#490), keeping the choices made (#497).
   Plain script: no inline code (the pages' CSP allows scripts from js/ only); from other files it needs util.js (el,
   icon, kindIcon, CAT_OF, keepDates, aboutWords for the ⓘ, #280, sayIn, announce, smoothly) and dates.js. */
"use strict";
(function () {
  const STAGES = {queued: "Waiting for another import to finish…", detecting: "Recognising the files…",
                  parsing: "Reading the records…", saving: "Saving the records…"};
  const STEPS = {send: "Sending", unpack: "Unpacking", find: "Recognising", review: "Review", saved: "Saved"};
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
  const plural = (n, [one, many]) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
  const day = s => { const t = dayTs(s); return isNaN(t) ? s : fmtD(t); };   // dates.js

  function host(container) {
    let h = container;
    if (typeof h === "string") h = document.querySelector(h);
    if (!h) h = document.querySelector("#import-status");
    if (!h) { h = node("section", {id: "import-status"}); (document.querySelector("main") || document.body).append(h); }
    return h;
  }

  /* the card (#281). opts {container, title, size, steps, pid}: steps, the STEPS it goes through (a zip's upload all
     of them; a file, several or a folder has nothing to unpack, #489, #497); pid, the profile it imports into (data-pid: Sources puts
     away a review for another profile once another is chosen, #490).
     -> {card, body, title, step(id, text), text(t), note(t), progress(v), end(how), focus(), hold()}. card is the whole card,
     body its job's own part (.import-job), which the rest of this file fills and calls the card (FRAMES finds its
     frame). step marks the stages before it done and shows and says text; text(t) changes the line without saying it
     (a count as it goes); note(t) adds to what's said beside the title ("2 files once unpacked"); progress(v): a
     fraction, null while there's nothing to measure (the bar moves to and fro), false for no bar; end("done") marks
     them all done, end("failed") or end("cancelled") the stage it stopped at */
  const FRAMES = new WeakMap();
  function frame(opts) {
    opts = opts || {};
    const ids = opts.steps || Object.keys(STEPS);
    const lis = ids.map(id => node("li", {}, node("span", {class: "job-dot", "aria-hidden": "true"}), STEPS[id], node("span", {class: "vh"})));
    const bar = node("progress", {class: "job-bar", max: "1", "aria-label": "Progress"});
    const now = node("p", {class: "job-now"});
    const body = node("div", {class: "import-job", tabindex: "-1"});
    const about = node("span", {class: "muted", text: opts.size || ""});
    const card = node("div", {class: "card job", "data-pid": opts.pid ?? null},
      node("div", {class: "job-hd"}, node("h3", {text: opts.title || "Import"}), about),
      node("ol", {class: "job-steps", "aria-label": "Stages"}, lis), bar, now, body);
    host(opts.container).append(card);
    let at = 0;
    const mark = (upto, how) => lis.forEach((li, i) => {
      const done = i < upto || i === upto && how === "done", bad = i === upto && how === "bad";
      li.className = done ? "done" : bad ? "bad" : i === upto ? "now" : "";
      if (i === upto && !how) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
      li.firstChild.replaceChildren(...(done ? [icon("check")] : bad ? [icon("x")] : []));
      li.lastChild.textContent = done ? " (done)" : bad ? " (stopped)" : "";
    });
    const f = {card, body, title: opts.title || "Import",
      step(id, text) {
        at = Math.max(0, ids.indexOf(id));
        card.dataset.step = id;
        mark(at);
        now.hidden = false;
        if (text) sayIn(now, text, false, {say: !card.hidden});   // (said unless put away for another profile, #497)
      },
      text(t) { now.textContent = t; },
      note(t) { about.textContent = [about.textContent, t].filter(Boolean).join(" · "); },
      progress(v) {
        bar.hidden = v === false;
        if (v == null || v === false) bar.removeAttribute("value"); else bar.value = Math.max(0, Math.min(1, v));
      },
      end(how) {
        f.progress(false);
        card.dataset.step = how;
        mark(how === "done" ? ids.length - 1 : at, how === "done" ? "done" : "bad");
        now.hidden = true;
      },
      // focus on the line saying what's happening, where it was on the card's button just gone (f.held, #489)
      hold() {
        const a = document.activeElement;
        if (!f.held || a && a !== document.body && !card.contains(a)) return;
        now.tabIndex = -1;
        now.focus({preventScroll: true});
      },
      // focus to the review: from where the files were chosen or dropped (the card's own section or dialog), or from
      // nowhere in particular, not from wherever someone has gone since
      focus() {
        const a = document.activeElement, from = card.parentElement.closest("section, dialog") || card.parentElement;
        if (a && a !== document.body && !from.contains(a) || !card.checkVisibility()) return false;
        body.focus({preventScroll: true});
        card.scrollIntoView({block: "start", behavior: smoothly()});   // the card's top under the top bar (scroll-padding)
        return true;
      }};
    FRAMES.set(body, f);
    f.step(ids[0]);
    f.progress(null);
    return f;
  }
  // an import started from files already on the server: in the upload's card (frame), else in a card of its own
  // from Recognising on
  const own = opts => opts.frame || frame({container: opts.container, title: opts.title, steps: ["find", "review", "saved"], pid: opts.profileId});
  /* a mark on the card for Sources (#283), on its job's own part, which its scripts look for, and on the whole card */
  const tag = (card, k, v) => { card.dataset[k] = v; const f = FRAMES.get(card); if (f) f.card.dataset[k] = v; };

  // what came in, counted as the review counts it (counted, below: a lab draw left out where there are results, #490)
  const countItems = c => counted(c).map(x => node("li", {text: plural(x.n, x.words) + (x.k === "results" ? " in " + plural(c.series || 0, ["test", "tests"]) : "")}));
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
  // suggestion first), or left empty to keep it a series of its own. -> {part, picked, wrong, kept, put}
  function labTable(j) {
    const listed = j.labs || [];
    if (!listed.length) return {part: null, picked: () => ({}), wrong: () => null, kept: () => ({}), put: () => {}};
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
                 : l.same ? `This profile has a series of this name now (${l.same}): left empty, its results go there.`   // (saved meanwhile, #497)
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
        node("td", {class: "num", "data-label": "Results"}, (l.n || 0).toLocaleString("en-US")),
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
    // what's been typed, by ref, and typed again in a review drawn again (#497): a name still listed gets it back,
    // marked as naming no series only where the person's own change had marked it (#504: not one half typed)
    const kept = () => Object.fromEntries(rows.filter(r => r.box.value !== r.initial).map(r => [r.l.ref, [r.box.value, r.box.hasAttribute("aria-invalid")]]));
    const put = typed => { for (const r of rows) if (r.l.ref in typed) {
      const [v, bad] = typed[r.l.ref];
      r.box.value = v;
      if (bad) r.box.dispatchEvent(new Event("change"));
    } };
    return {part, picked, wrong, kept, put};
  }

  // results that look like ones already in the profile from another source (#96): flagged, never merged unless ticked
  // when each was taken, in the person's zone and date format, its time kept whole (dates.js, #522: not the browser's)
  const when = t => { t = String(t); const ms = zParse(t); if (!isFinite(ms)) return t;
    return t.length === 10 || /T(00:00|12:00):00$/.test(t) ? fmtD(ms) : fmtDT(ms); };
  const valueText = x => `${x.value} ${x.unit || ""}`.trim();
  function sameChoices(j) {
    const listed = j.possible || [];
    if (!listed.length) return {part: null, picked: () => [], tick: () => {}};
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
    return {part, picked: () => boxes.filter(b => b.checked).map(b => b.value),
            tick: refs => { for (const b of boxes) if (refs.includes(b.value)) b.checked = true; }};   // drawn again (#497)
  }

  /* how many records are already here: one count for the bar and the list (#490), a lab draw left out where there
     are results, as what's new leaves it out (counted, below): they're its */
  const draws = j => (j.total || j.counts || {}).results > 0;
  const hereCount = j => { const d = j.duplicates || {};
    return Math.max(0, (d.events || 0) - (draws(j) ? (d.kinds || {}).lab || 0 : 0) + (d.results || 0) + (d.other || 0)); };
  // ...and with a wearable's data there already (#500): the bar's count, and the list's (#504)
  const allHere = j => { const w = (j.duplicates || {}).watch || {}; return hereCount(j) + [...WATCH].reduce((a, k) => a + (w[k] || 0), 0); };
  // the duplicates of what the profile has (#22): skipped, each with a box to add it again anyway; a series' results
  // dated from the first to the last (#490), a lab draw not offered beside its own results
  function dupChoices(j) {
    const d = j.duplicates || {items: []}, n = hereCount(j), all = allHere(j), boxes = new Map();
    const items = (d.items || []).filter(it => !(it.kind === "lab" && draws(j)));   // a draw goes with its results
    const watch = watchHere(d.watch);   // a wearable's days and workouts there already (#497), named after the list
    const dups = plural(all, ["duplicate", "duplicates"]);
    const part = items.length ? node("details", {class: "imp-dups"},
      node("summary", {text: `Already here: ${dups}, skipped unless ticked`}),
      node("p", {class: "hint", text: "These are in the profile already. Tick any you want added again anyway; an allergy, problem or " +
        "appointment already in its lists isn't added twice."}),
      node("ul", {}, items.map(it => {
        // an entry of the profile's lists has no ref (#497): named, with no box
        const b = it.ref ? node("input", {type: "checkbox", value: it.ref}) : node("span", {class: "imp-dup-none"});
        if (it.ref) boxes.set(it, b);
        const kind = (KIND[it.kind] || LISTED[it.kind] || [it.kind])[0];
        const what = it.kind === "results" ? plural(it.n || 0, ["lab result", "lab results"]) + ": " + it.title
                   : it.title.toLowerCase().startsWith(kind + ":") ? it.title : kind + ": " + it.title;   // ("Allergy: Peanuts" says so)
        const when = it.t ? keepDates(it.to ? `${day(it.t)} – ${day(it.to)}` : day(it.t)) : "Undated";
        return node("li", {}, node(it.ref ? "label" : "span", {}, b, " ", node("span", {class: "d"}, when), " ", what));
      })),
      d.more ? node("p", {class: "hint", text: `…and ${d.more.toLocaleString("en-US")} more.`}) : null,
      watch ? node("p", {class: "hint", text: `Of these, not listed and always skipped: ${watch}.`}) : null)
      : all ? node("p", {class: "hint", text: !n ? `Already here, skipped: ${watch}.` : `Already here: ${dups}, skipped` + (watch ? `, ${watch} among them.` : ".")})
      : null;
    // what the ticked ones add, by count key: a result's n results, an event one of its kind
    const ticked = () => items.filter(it => boxes.get(it)?.checked);
    // the boxes ticked, by ref, and ticked again in a review drawn again (#497)
    const kept = () => ticked().map(it => it.ref);
    const tick = refs => { for (const [it, b] of boxes) if (refs.includes(it.ref)) b.checked = true; };
    return {part, picked: kept, ticked, kept, tick};
  }
  // the profile-wide lists' entries a duplicate can be (#497)
  const LISTED = {allergy: ["allergy", "allergies"], problem: ["problem or diagnosis", "problems or diagnoses"],
                  upcoming: ["upcoming appointment", "upcoming appointments"]};
  // what of a wearable's data is there already, in words ("2 days of activity data and 1 workout"), or ""
  function watchHere(w) {
    const said = EXTRA.filter(([k]) => (w || {})[k] > 0).map(([k, words]) => plural(w[k], words));
    return said.length > 1 ? said.slice(0, -1).join(", ") + " and " + said.at(-1) : said[0] || "";
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
  // a wearable's (the server's jobs._watch_counts), counted in the review's bar beside the records (#500)
  const WATCH = new Set(["watch_days", "workouts", "ecgs", "rhythm", "bp_readings", "temp_readings", "glucose_readings", "cgm_days", "heights", "cycle"]);
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
        node("span", {class: "val", text: x.n.toLocaleString("en-US")}),
        x.k === "results" ? node("span", {class: "delta", text: "in " + plural(c.series || 0, ["test", "tests"]) +
          ((c.kinds || {}).lab ? ", from " + plural(c.kinds.lab, KIND.lab) : "")}) : null,
        some.length ? node("ul", {}, some.map(([t, what]) => node("li", {}, t ? node("span", {class: "d", text: day(t)}) : null, " ", what)),
                           x.n > some.length ? node("li", {class: "more", text: `and ${(x.n - some.length).toLocaleString("en-US")} more`}) : null) : null);
    }));
  }

  // how many records are new, already here or possibly the same as another source's: a bar in three parts and its key.
  // A wearable's data counts beside them (#500: a later export's new day and workout, and those it has already)
  function split(j) {
    const c = j.counts || {}, same = (j.possible || []).length;
    const records = counted(c).filter(x => x.record || WATCH.has(x.k)).reduce((a, x) => a + x.n, 0);
    const here = allHere(j);
    const parts = [["new", Math.max(0, records - same), "new"], ["here", here, "already here"], ["same", same, "possibly the same"]];
    const total = parts.reduce((a, p) => a + p[1], 0);
    if (!total) return null;
    let x = 0;
    const bar = el("svg:svg", {class: "imp-bar", viewBox: `0 0 ${total} 1`, preserveAspectRatio: "none", "aria-hidden": "true", focusable: "false"},
      parts.filter(p => p[1]).map(([k, n]) => { const r = el("svg:rect", {class: k, x, y: 0, width: n, height: 1}); x += n; return r; }));
    return node("div", {class: "imp-split"}, bar,
      node("ul", {}, parts.map(([k, n, words]) => node("li", {class: k}, node("b", {text: n.toLocaleString("en-US")}), " " + words))));
  }

  // the button's words: what it adds (#282), "Add 6 lab results", or null when it adds nothing
  function addWords(c) {
    const list = counted(c).filter(x => x.k !== "documents");
    if (!list.length) return null;
    if (list.length === 1) return "Add " + plural(list[0].n, list[0].words);
    const records = list.filter(x => x.record);
    return records.length === list.length ? "Add " + plural(records.reduce((a, x) => a + x.n, 0), ["record", "records"]) : "Add all of this";
  }

  /* the review. Drawn again when the profile's records may have changed under it (f.fresh: opened again from
     Waiting for you, or another import of the profile saved on the page, #490): the server reads the preview again,
     so the same file saved meanwhile shows as already here; the choices made keep (#497): a series typed, a
     duplicate or a possible match ticked, where it's still offered */
  function jobReview(job, card) {
    const pid = job.profile_id, base = `profiles/${encodeURIComponent(pid)}/jobs/${job.id}`, f = FRAMES.get(card);
    return new Promise(done => {
      let shown = null;   // the choices drawn: kept when it's drawn again (#497)
      function draw(j, again) {
        const range = j.range ? (j.range.from === j.range.to ? day(j.range.from) : `${day(j.range.from)} – ${day(j.range.to)}`) : "";
        const labs = labTable(j), same = sameChoices(j), dups = dupChoices(j);
        if (shown) { labs.put(shown.labs.kept()); dups.tick(shown.dups.kept()); same.tick(shown.same.picked()); }
        shown = {labs, same, dups};
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
        // -> true: done; false: refused, said; null: cancelled meanwhile (#542: cycle tracking turned off in another
        // tab), the card then saying so as Cancel would
        const act = async (what, body) => {
          let r;
          try { r = await call(`${base}/${what}`, {method: "POST", body: JSON.stringify(body || {})}); }
          catch (e) { say("Couldn't reach the server. Check the connection and try again."); return false; }
          if (r.status === 401) { location.replace("login.html"); return false; }
          if (!r.ok) {
            const why = await detail(r);
            const now = r.status === 409 ? await call(base).then(g => g.ok ? g.json() : null).catch(() => null) : null;
            if (now?.state === "discarded") { showCancelled(card, now.message); done(now); return null; }
            say(why);
            return false;
          }
          return true;
        };
        // focus on the button pressed stays in the card while it changes under it (#489): while it's saved, on the
        // line saying so; then on the line saying how it ended (result, below). A button pressed in vain keeps it
        const back = b => { if (f.held && !card.contains(document.activeElement)) b.focus({preventScroll: true}); };
        add.addEventListener("click", async () => {
          const bad = labs.wrong();
          if (bad) { say("A lab name has a series that isn't there: pick one from its list, or leave it empty."); bad.focus(); return; }
          f.held = card.contains(document.activeElement);
          busy(true); say("Adding…");
          const ok = await act("confirm", {keep: dups.picked(), labs: labs.picked(), same: same.picked()});
          if (ok === null) return;
          if (!ok) { busy(false); back(add); return; }
          card.replaceChildren();
          f.step("saved", STAGES.saving);
          f.progress(null);
          f.hold();
          done(poll(card, pid, j.id, STAGES.saving));
        });
        cancel.addEventListener("click", async () => {
          f.held = card.contains(document.activeElement);
          busy(true); say("Cancelling…");
          const ok = await act("discard");
          if (ok === null) return;
          if (!ok) { busy(false); back(cancel); return; }
          showCancelled(card);
          done(j);
        });
        fill(card,
          node("p", {class: "imp-state", text: stateWords(j)}),
          node("h3", {class: "imp-title"}, keepDates([j.label || j.importer, range].filter(Boolean).join(" · "))),
          node("p", {class: "hint", text: j.new ? "Nothing is saved until you add it with the button at the foot."
            : sgNothing(j) ? "Nothing in this upload is left to add to this profile. Cancel it to remove the upload."
            : "Everything in this upload is in this profile already. Tick a duplicate to add it again anyway, or cancel."}),
          j.new ? tiles(j) : null, split(j), labs.part, same.part, dups.part, notes(j),
          node("div", {class: "sg-bar imp-actions", role: "region", "aria-label": "Add or cancel this import"},
            node("span", {class: "sg-bar-what"}, node("span", {class: "sg-bar-n", text: "Not saved yet"}), msg),
            node("span", {class: "sg-bar-acts"}, cancel, add)));
        barRoom(card.querySelector(".imp-actions"));
        if (again) return;
        // the stage line says Review (#281); its "Ready to import" is said by focus on it, or else here, unless the
        // card is put away for another profile (Sources, #497): nothing is said of a card no one sees
        f.step("review");
        f.text("");
        f.progress(false);
        if (!f.focus() && !f.card.hidden) announce(`${stateWords(j)}: ${f.title}. Nothing is saved until you add it.`);
      }
      f.fresh = async () => {
        let j = null;
        try { const r = await call(base); if (r.ok) j = await r.json(); } catch (e) {}
        if (j?.state === "discarded" && f.card.dataset.step === "review") { showCancelled(card, j.message); done(j); return; }   // (#539: nothing left once cycle tracking was turned off)
        if (!j || j.state !== "preview" || f.card.dataset.step !== "review") return;   // (saved or cancelled meanwhile)
        // each list's open state, and focus on the same control, found again by what it's for (#500), where it's
        // still offered; else on the card
        const a = document.activeElement, had = card.contains(a), back = had && a !== card ? spot(card, a) : null;
        const open = [...card.querySelectorAll("details")].map(d => [listSel(d), d.open]);
        draw({profile_id: pid, ...j}, true);
        for (const [sel, o] of open) { const d = card.querySelector(sel); if (d) d.open = o; }
        if (had) { const to = back && back(); (to && !to.disabled && to.checkVisibility() ? to : card).focus({preventScroll: true}); }
      };
      draw(job);
    });
  }
  // what a review says it is: one with nothing in it (#534: its cycle records left out once tracking was turned off)
  // isn't all duplicates
  const stateWords = j => j.new ? "Ready to import" : sgNothing(j) ? "Nothing left to import" : "All duplicates";
  // a review drawn again (f.fresh, above): one opened again (Sources' Waiting for you), or each other one of the
  // profile on the page once an import of it is saved
  const fresh = card => FRAMES.get(card)?.fresh?.();
  // a review's list (the lab names, possible matches, duplicates, the notes), as a selector that finds it drawn again
  const listSel = d => d.classList.length ? "details." + d.classList[0] : "details:not([class])";
  // the control a, found again in the card drawn again (#500): a box by its ref, a list's summary, the bar's Add or
  // Cancel by which it is (#504: a list gone, its ⓘ with it, moves the places of the rest), else the same kind of
  // control at the same place in its list, or among the card's outside the lists -> () => the element or undefined
  // (f.fresh puts focus on the card when it's gone or disabled)
  function spot(card, a) {
    const d = a.closest("details"), at = d ? listSel(d) + " " : "";
    const sel = a.matches("summary") ? at + "summary"
      : a.matches("input[data-ref]") ? at + `input[data-ref="${CSS.escape(a.dataset.ref)}"]`
      : a.matches("input[type=checkbox]") ? at + `input[type=checkbox][value="${CSS.escape(a.value)}"]`
      : a.matches(".imp-actions button") ? ".imp-actions " + (a.matches(".primary") ? ".primary" : "button:not(.primary)") : null;
    if (sel) return () => card.querySelector(sel);
    const all = () => [...card.querySelectorAll(at + a.localName)].filter(x => d || !x.closest("details")), i = all().indexOf(a);
    return () => all()[i];
  }
  const freshOthers = (card, pid) => { for (const c of document.querySelectorAll(`.job[data-step=review][data-pid="${pid}"] .import-job`)) if (c !== card) fresh(c); };

  // replaceChildren turns a null into the text "null"; parts that aren't there are left out instead
  function fill(card, ...parts) { card.replaceChildren(...parts.filter(p => p != null)); }
  /* the room the review's bar takes at the foot of what scrolls (the page, or a dialog's body), measured (#489), so
     what takes focus is scrolled clear of it (--sg-bar-room, app.css): the tallest bar there, with its gap below */
  const ROOM = new ResizeObserver(es => { for (const e of es) {
    if (!e.target.isConnected) ROOM.unobserve(e.target);
    const at = e.target.sgRoom, bars = [...at.querySelectorAll(".sg-bar:not([hidden])")].filter(b => b.offsetHeight);
    if (!bars.length) { at.style.removeProperty("--sg-bar-room"); continue; }
    const pad = at === document.documentElement ? 0 : parseFloat(getComputedStyle(at).paddingBottom) || 0;   // (a sticky bar keeps clear of it)
    // its height and what's under it: the gap, and a phone's tabs (its bottom, app.css)
    placeAt(at, {"--sg-bar-room": Math.ceil(pad + Math.max(...bars.map(b => b.offsetHeight + (parseFloat(getComputedStyle(b).bottom) || 0)))) + 8});
  } });
  function barRoom(bar) {
    bar.sgRoom = bar.closest(".sheet-body") || document.documentElement;
    ROOM.observe(bar);
  }
  /* the line saying how it ended takes focus where focus was in the card, or was on its Add or Cancel and went
     nowhere else meanwhile (#489: the button pressed is gone) */
  function result(card, line) {
    const f = FRAMES.get(card), a = document.activeElement;
    if (!f.card.contains(a) && !(f.held && (!a || a === document.body))) return;
    f.held = false;
    line.tabIndex = -1;
    line.focus();
  }
  /* a card whose import is over (saved, failed or cancelled): Sources clears it once another section is shown (#283) */
  const settled = card => tag(card, "settled", "");

  function showDone(card, pid, j) {
    settled(card);
    freshOthers(card, pid);
    FRAMES.get(card).end("done");
    const c = {...j.counts, ...(j.cycle ? {cycle: j.cycle} : {})}, items = countItems(c), sk = j.skipped || {};   // (cycle: #534)
    // the duplicates skipped, in the review's one count and its words (#508: allHere, a wearable's data there already
    // among them; a lab draw with its results, #497): "10 duplicates skipped, 2 days of activity data and 1 workout among them."
    const was = {duplicates: sk, counts: {results: (c.results || 0) + (sk.results || 0)}};
    const skipped = hereCount(was), all = allHere(was), watch = watchHere(sk.watch);
    const ln = j.lab_names || {};
    const said = node("p", {}, node("b", {text: "Imported."}), ` Read as ${j.label || j.importer}${rangeText(j.range)}.`);
    const dup = !all ? [] : [!skipped ? `${cap(watch)} already here, not added again.`
      : `${plural(all, ["duplicate", "duplicates"])} skipped` + (watch ? `, ${watch} among them.` : ".")];
    fill(card, said,
      items.length ? node("ul", {}, items) : node("p", {text: dup.length ? "Nothing new: it was all in the profile already." : "The files held no records to show."}),
      dup.length ? node("p", {class: "hint", text: dup.join(" ")}) : null,
      ln.matched ? node("p", {class: "hint", text: `${plural(ln.matched, ["lab name", "lab names"])} put in an existing series.`}) : null,
      j.linked ? node("p", {class: "hint", text: `${plural(j.linked, ["result", "results"])} shown once, as the same result from another source.`}) : null,
      notes(j),
      node("p", {}, node("a", {href: `index.html?profile=${pid}#timeline`, text: "See them on the timeline"})));   // its profile and tab (#208)
    result(card, said);
    // said by a toast (#263), with the way to what came in and an Undo, which asks first in Import history's words
    // (#360: it can't be reversed): the card stays as it is
    const what = items.map(li => li.textContent);
    const most = counted(c).map((x, i) => [x.n, what[i]]).sort((a, b) => b[0] - a[0]).map(x => x[1]);   // the largest first (#497)
    const labs = c.results > 0, where = labs ? "labs" : "timeline";
    const t = toast(what.length ? `Imported ${most.slice(0, 2).join(", ")}${what.length > 2 ? " and more" : ""}.` : "Imported: nothing new.", {
      actions: [{text: labs ? "Open Labs" : "Open the timeline", href: `index.html?profile=${pid}#${where}`},
        ...(j.import_id != null && what.length ? [{text: "Undo…", onClick: () => undoImport(card, pid, j)}] : [])]});
    if (j.import_id != null) {
      tag(card, "saved", j.import_id);   // which import it saved: undone elsewhere, it says so (told, #513)
      undoable(t, j.import_id);
      told(pid, j.import_id, false);   // Import history read again (#508: a save from Waiting for you too), the guide's steps
    }
  }
  /* a toast with an import's Undo (an upload's, Type them in's), marked with the import, for told to take its Undo away
     once the import is undone elsewhere (#513) */
  function undoable(t, id) { if (t?.node) t.node.dataset.undoImport = id; }
  const undoneLine = () => node("p", {}, node("b", {text: "Undone."}), " The import's records are gone from the profile; Import history lists it as undone.");
  /* an import undone, wherever it was (#513: Import history's Undo, another toast's): its toasts offer no Undo now and
     say it was undone, and its saved cards say so, focus kept on what's left of them (not the toast whose Undo is
     running: it goes once that's done, undoImport); the profile's other reviews drawn again, as what the import brought
     is no longer a duplicate */
  function undoneElsewhere(pid, id) {
    for (const t of document.querySelectorAll(`#sgToasts .sg-toast[data-undo-import="${id}"]`)) {
      const b = [...t.querySelectorAll(".sg-toast-acts button")].find(x => x.textContent === "Undo…");
      if (!b || b.getAttribute("aria-disabled") === "true") continue;
      // its Open link goes with its Undo (#517: "Open Labs" to records that are gone)
      const acts = b.closest(".sg-toast-acts"), had = acts.contains(document.activeElement);
      acts.remove();
      t.querySelector(".sg-toast-text").textContent = "Undone: the import's records are gone from the profile.";
      if (had) t.querySelector("button").focus();
    }
    for (const c of document.querySelectorAll(`.import-job[data-saved="${id}"]`)) {
      const had = c.contains(document.activeElement), line = undoneLine();
      c.replaceChildren(line);
      if (had) { line.tabIndex = -1; line.focus(); }
    }
    // Type records in's line saying what was saved, and its links (#517: "Saved 1 result. See them on Labs" stayed)
    for (const m of document.querySelectorAll(`[data-saved-line="${id}"]`)) {
      const had = m.contains(document.activeElement);
      sayIn(m, "Undone: what was saved is gone from the profile.", false, {say: false});
      delete m.dataset.savedLine;
      if (had) { m.tabIndex = -1; m.focus(); }
    }
    freshOthers(null, pid);
  }
  /* an import's counts in Import history's words (import-history.js lists them: "1 lab draw, 1 lab result in 1 test");
     the Undo's question says them too (#508), with a lab draw left out where there are results (draws false), as the
     review and the toast count it (counted, above) */
  const HKIND = {visit: ["visit", "visits"], lab: ["lab draw", "lab draws"], imaging: ["imaging study", "imaging studies"],
                 procedure: ["procedure", "procedures"], study: ["report", "reports"], message: ["message", "messages"],
                 letter: ["letter", "letters"], document: ["document", "documents"], stay: ["hospital stay", "hospital stays"],
                 med: ["medication", "medications"], rx: ["pharmacy fill", "pharmacy fills"], claim: ["claim", "claims"],
                 immunization: ["immunization", "immunizations"]};   // #179
  // records typed in, by kind (the save's counts.typed, routes_manual.py), in Type records in's words (#517: a problem,
  // an appointment and a payment were "No records", a vitals entry "3 lab results in 3 tests"); readings at home as
  // one count, as before (#224); a height is said below
  const TYPED = {...HKIND, labs: ["lab result", "lab results"], study: ["report or study", "reports or studies"],
                 claim: ["insurance claim", "insurance claims"], problem: LISTED.problem, upcoming: LISTED.upcoming, payment: ["payment", "payments"],
                 vitals: ["vitals entry", "vitals entries"], home: ["weight or steps entry at home", "weight or steps entries at home"],
                 workout: ["workout", "workouts"], readings: ["reading taken at home", "readings taken at home"]};
  const AT_HOME = ["home_bp", "home_glucose", "home_temp", "home_spo2"];
  function importCounts(c, draws = true) {
    c = c || {};
    const out = [];
    if (c.typed) {
      const typed = Object.entries(c.typed).filter(([k, n]) => n > 0 && k !== "height" && !AT_HOME.includes(k));
      const home = AT_HOME.reduce((n, k) => n + (c.typed[k] || 0), 0);
      if (home) typed.push(["readings", home]);
      // "in 2 tests" only where the results are all lab results: vitals are charted as results too
      out.push(...typed.sort((a, b) => b[1] - a[1]).map(([k, n]) => plural(n, TYPED[k] || [k, k])
        + (k === "labs" && !c.typed.vitals && c.series ? " in " + plural(c.series, ["test", "tests"]) : "")));
      if (c.heights_set) out.push(plural(c.heights_set, ["height", "heights"]) + " put in About this person (kept there if this is undone)");
      return out.length ? out.join(", ") : "No records";
    }
    const listed = Object.entries(c.kinds || {}).filter(([k]) => k !== "labitem").sort((a, b) => b[1] - a[1]);
    const kinds = listed.filter(([k]) => !(k === "lab" && !draws && c.results));
    if (kinds.length) out.push(...kinds.map(([k, v]) => plural(v, HKIND[k] || [k, k])));
    else if (c.events && !listed.length) out.push(plural(c.events, ["event", "events"]));
    if (c.results) out.push(plural(c.results, ["lab result", "lab results"]) + " in " + plural(c.series || 0, ["test", "tests"]));
    if (c.documents) out.push(plural(c.documents, ["attached file", "attached files"]));
    if (c.allergies) out.push(plural(c.allergies, ["allergy", "allergies"]));   // #179 (jobs.summarise)
    if (c.problems) out.push(plural(c.problems, ["problem or diagnosis", "problems or diagnoses"]));
    if (c.upcoming) out.push(plural(c.upcoming, ["upcoming appointment", "upcoming appointments"]));
    if (c.payments) out.push(plural(c.payments, ["payment", "payments"]));   // typed in before its counts were kept (#517)
    if (c.entries) out.push(plural(c.entries, ["problem, appointment or weight", "problems, appointments or weights"]));
    if (c.watch_days) out.push(plural(c.watch_days, EXTRA.find(x => x[0] === "watch_days")[1]));   // as the review says it (#534)
    if (c.workouts) out.push(plural(c.workouts, ["workout", "workouts"]));
    if (c.readings) out.push(plural(c.readings, ["reading taken at home", "readings taken at home"]));   // typed in (#224)
    if (c.heights_set) out.push(plural(c.heights_set, ["height", "heights"]) + " put in About this person (kept there if this is undone)");
    if (c.ecgs) out.push(plural(c.ecgs, ["ECG recording", "ECG recordings"]));   // #182
    if (c.rhythm) out.push(plural(c.rhythm, ["irregular rhythm notification", "irregular rhythm notifications"]));
    if (c.bp_readings) out.push(plural(c.bp_readings, ["blood pressure reading", "blood pressure readings"]));   // #223 (never cycle records)
    if (c.temp_readings) out.push(plural(c.temp_readings, ["body temperature reading", "body temperature readings"]));
    if (c.glucose_readings) out.push(plural(c.glucose_readings, ["glucose meter reading", "glucose meter readings"]));
    if (c.cgm_days) out.push(plural(c.cgm_days, ["day of CGM readings", "days of CGM readings"]));
    if (c.heights) out.push(plural(c.heights, ["height", "heights"]));
    return out.length ? out.join(", ") : "No records";
  }
  /* what an Undo asks, of an import as Import history has it (e, GET imports' entry), in one wording wherever it's
     asked (#504; #508: the toast's, Type them in's and Import history's): what goes, in one count (importCounts), the
     files uploaded for it only when it was uploaded, and the lab-name matches its review made, as many as undoing it
     would forget now (#500, the server's labmap.forgettable: not those another import has used since); e.cycle: the
     cycle records it saved, which only the toast of the person who saved them knows (#534) */
  function undoAsks(e) {
    const n = e.lab_matches || 0, typed = e.origin === "manual";
    const what = [importCounts(e.counts, false), e.cycle && plural(e.cycle, EXTRA.find(x => x[0] === "cycle")[1])].filter(w => w && w !== "No records").join(", ");
    return `Everything this ${typed ? "typed-in" : e.label} import brought is removed${what ? ": " + what.toLowerCase() : ""}` +
      (e.upload != null ? ", and the files uploaded for it" : "") + "." +
      (!n ? "" : n === 1 ? " The lab-name match made for it is forgotten too: the next import of that name asks about it again."
       : ` The ${n.toLocaleString("en-US")} lab-name matches made for it are forgotten too: the next import of those names asks about them again.`) +
      ` This can't be reversed; you can ${typed ? "type them in" : "import the files"} again.`;
  }
  /* an import saved or undone, told to the page (#508) as document's "sg:imports", detail {pid, id, undone, wait(p),
     focus}: Import history reads its list again, the guide its steps, the records page its records (an empty profile's
     card back). Each gives wait() what it's doing, and after an Undo the page may name where focus goes when the card
     isn't shown (focus). -> the detail, once they're done */
  function told(pid, id, undone) {
    if (undone) undoneElsewhere(pid, id);
    const waits = [], detail = {pid: +pid, id, undone, wait: p => waits.push(Promise.resolve(p).catch(() => {})), focus: null};
    document.dispatchEvent(new CustomEvent("sg:imports", {detail}));
    return Promise.all(waits).then(() => detail);
  }
  /* an Undo from a toast (an upload's card's, Type them in's): asked first as Import history asks it, of the import as
     the history has it now (undoAsks; fallback, as the toast knows it, if the list can't be read), then undone and the
     page told (told). -> false: kept; else told's detail. Throws what went wrong, which the toast says */
  async function undoAsk(pid, id, fallback, cycle) {
    const path = `profiles/${encodeURIComponent(pid)}/imports`;
    const now = await api(path).then(r => r.ok ? r.json() : null).catch(() => null);
    const list = (now || {}).imports || [], e = list.find(x => x.id === id && x.state === "active") || list.find(x => x.id === id) || fallback;
    if (e.state === "undone") throw new Error("This import was undone already.");   // (elsewhere, #513: not asked about)
    if (!await ask({title: "Undo this import?", danger: true, action: "Undo import", cancel: "Keep it", message: undoAsks({...e, cycle})})) return false;
    // the import asked about, by when it was saved too: never another that has its id (#512, from before ids stayed
    // their own); one undone already is refused
    const r = await api(`${path}/${id}/undo${e.state === "active" ? "?at=" + e.at : ""}`, {method: "POST"});
    if (!r.ok) { const x = await r.json().catch(() => ({})); throw new Error(typeof x.detail === "string" ? x.detail : `the server answered ${r.status}`); }
    return told(pid, id, true);
  }
  /* focus after a toast's Undo: on line, the one saying it's undone, where it's shown (#504: not the next toast's ✕);
     else (the card put away: the upload dialog closed) once the toast has gone and given focus back where it came
     from, if that left it on a toast or nowhere (#508), where the page names (told's focus), else its first heading */
  function undoneFocus(line, said) {
    const lost = () => { const a = document.activeElement; return !a || a === document.body || !!a.closest("#sgToasts"); };
    if (line && lost() && line.checkVisibility()) { line.tabIndex = -1; line.focus(); return; }
    setTimeout(() => {
      if (!lost()) return;
      const to = said?.focus || [...document.querySelectorAll("main h1, main h2")].find(h => h.checkVisibility() && !h.matches(".vh"));
      if (!to) return;
      if (to.matches("h1, h2, h3")) to.tabIndex = -1;
      to.focus({preventScroll: true});
      to.scrollIntoView({block: "nearest"});
    });
  }
  /* the Undo of an upload's toast (undoAsk), the card then saying it's undone, and focus on that line (undoneFocus) */
  async function undoImport(card, pid, j) {
    // its cycle records as they are now (#542): none once tracking is turned off or they're deleted
    const now = await call(`profiles/${encodeURIComponent(pid)}/jobs/${j.id}`).then(r => r.ok ? r.json() : null).catch(() => null);
    const said = await undoAsk(pid, j.import_id, {label: j.label || j.importer, counts: j.counts, upload: j.upload_id ?? null}, now?.cycle);
    if (!said) return false;
    const line = undoneLine();
    card.replaceChildren(line);
    undoneFocus(line, said);
    toast("Undone: the import's records are gone from the profile.");
  }

  function showFailed(card, text) {
    // the message is plain text; any address in it (the issues page) becomes a link, nothing else is markup
    const parts = String(text || "The import failed; nothing was saved.").split(/(https:\/\/[^\s)]+[^\s).,])/);
    const said = node("p", {}, node("b", {text: "Not imported. "}),
      parts.map((p, i) => i % 2 ? node("a", {href: p, target: "_blank", rel: "noopener noreferrer", text: p}) : p));
    card.replaceChildren(said);
    result(card, said);
    card.classList.add("error");
    settled(card);
    const f = FRAMES.get(card);
    f.end("failed");
    if (!f.card.hidden) announce("Not imported: " + f.title, {error: true});   // (one put away says nothing, #497)
  }

  async function uploaded(opts) {
    opts = opts || {};
    const pid = opts.profileId, up = opts.uploadId, f = own(opts), card = f.body;
    f.step("find", "Starting the import…");
    f.progress(null);
    let r;
    try {
      r = await call(`profiles/${encodeURIComponent(pid)}/uploads/${encodeURIComponent(up)}/import`,
                     {method: "POST", body: JSON.stringify(opts.importer ? {importer: opts.importer} : {})});
    } catch (e) { showFailed(card, "Couldn't reach the server to start the import. Check the connection and try again."); return null; }
    if (r.status === 401) { location.replace("login.html"); return null; }
    if (!r.ok) { showFailed(card, await detail(r)); return null; }
    return poll(card, pid, (await r.json()).id);
  }

  async function poll(card, pid, jid, said = null) {   // said: the stage's words said already
    tag(card, "job", jid);   // which import it is: Sources' "Waiting for you" opens the card there is, not another (#283)
    const f = FRAMES.get(card), began = Date.now();
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
        f.text("Lost touch with the server; still trying… (the import carries on there)");
      }
      if (j) {
        if (j.state === "done") { showDone(card, pid, j); return j; }
        if (j.state === "failed") { showFailed(card, j.message); return j; }
        if (j.state === "discarded") { showCancelled(card, j.message); return j; }
        if (j.state === "preview") return jobReview({profile_id: pid, ...j}, card);
        const secs = Math.round((Date.now() - began) / 1000);
        const stage = j.state === "queued" && j.stage !== "saving" ? "queued" : j.stage, words = STAGES[stage] || "Working…";
        if (words !== said) { f.step(stage === "saving" ? "saved" : "find", words); said = words; }   // said once, as it starts
        else f.text(words + (secs >= 5 ? ` (${secs} s)` : ""));
      }
      // about once a second at first; slower for a long import, and slower still while the server can't be reached
      const elapsed = Date.now() - began;
      wait = misses ? Math.min(10000, 1000 * 2 ** misses) : elapsed < 20000 ? 1000 : elapsed < 120000 ? 2000 : 5000;
    }
  }

  // why: the server's, for one it cancelled itself (#542: an upload holding nothing it could import)
  function showCancelled(card, why) {
    settled(card);
    FRAMES.get(card).end("cancelled");
    const said = node("p", {}, node("b", {text: "Cancelled."}), " " + (why || "Nothing was saved, and the upload was removed."));
    card.replaceChildren(said);
    result(card, said);
  }

  // an import someone else started (a connected source's first pull, connect.js), or one left at its preview (Sources'
  // "Waiting for you", #283, with opts.text: what it says until the preview is there): its progress and preview here
  function follow(opts) {
    const f = own(opts || {}), text = opts.text || STAGES.parsing;
    f.step("find", text);
    return poll(f.body, opts.profileId, opts.jobId, text);
  }

  // reviewsAgain(pid): the profile's open reviews read again, as after a save: cycle tracking turned on or off, or its
  // records deleted (sources-cycle.js, #530: a review kept offering the cycle records turning it off left out)
  window.sanguityImport = {uploaded, follow, frame, fresh, importCounts, undoAsks, undoAsk, undoneFocus, told, undoable, reviewsAgain: pid => freshOthers(null, pid)};
})();
