/* Sanguity · importing an upload in the background (#20). The import page (#19) calls
     window.sanguityImport.uploaded({profileId, uploadId[, container][, importer]})
   once an upload is finished. This starts the import, polls it (about once a second, slower the longer it runs),
   shows the stage, then its preview (#22): what it would add by kind, the dates it covers, and the duplicates of
   what the profile has, skipped unless ticked to keep, lab names Sanguity doesn't know with a series suggested for
   each (#23: confirm it, pick another or keep it separate; remembered for later imports), results possibly the same as
   one another source brought (#96: ticked to show once), with Save and Cancel. Saving polls again and shows what came
   in, with a link to the timeline; cancelling throws the import and its upload away.
   Self-contained plain script: no inline code (the pages' CSP allows scripts from js/ only), nothing it needs
   from other files. */
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
  // "More about this" (#176): the same toggle as util.js moreAbout(), built here so this file needs nothing else
  const more = text => node("details", {class: "more"}, node("summary", {text: "More about this"}), node("p", {text}));
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
    if (c.results) items.push(node("li", {text: plural(c.results, ["lab result", "lab results"]) + " in " + plural(c.series || 0, ["test", "tests"])}));
    if (c.documents) items.push(node("li", {text: plural(c.documents, ["attached file", "attached files"])}));
    // #179: what goes to the profile's lists (jobs.summarise)
    if (c.allergies) items.push(node("li", {text: plural(c.allergies, ["allergy", "allergies"])}));
    if (c.problems) items.push(node("li", {text: plural(c.problems, ["problem or diagnosis", "problems or diagnoses"])}));
    if (c.upcoming) items.push(node("li", {text: plural(c.upcoming, ["upcoming appointment", "upcoming appointments"])}));
    if (c.watch_days) items.push(node("li", {text: plural(c.watch_days, ["day of activity data", "days of activity data"])}));
    if (c.workouts && !(c.kinds || {}).exercise) items.push(node("li", {text: plural(c.workouts, KIND.exercise)}));
    if (c.ecgs) items.push(node("li", {text: plural(c.ecgs, ["ECG recording", "ECG recordings"])}));   // #182
    if (c.rhythm) items.push(node("li", {text: plural(c.rhythm, ["irregular rhythm notification", "irregular rhythm notifications"])}));
    // #223: an Apple Health export's home readings, glucose, heights and (only while cycle tracking is on) cycle records
    if (c.bp_readings) items.push(node("li", {text: plural(c.bp_readings, ["blood pressure reading", "blood pressure readings"])}));
    if (c.temp_readings) items.push(node("li", {text: plural(c.temp_readings, ["body temperature reading", "body temperature readings"])}));
    if (c.glucose_readings) items.push(node("li", {text: plural(c.glucose_readings, ["glucose meter reading", "glucose meter readings"])}));
    if (c.cgm_days) items.push(node("li", {text: plural(c.cgm_days, ["day of CGM readings", "days of CGM readings"])}));
    if (c.heights) items.push(node("li", {text: plural(c.heights, ["height, offered for About this person", "heights, offered for About this person"])}));
    if (c.cycle) items.push(node("li", {text: plural(c.cycle, ["cycle tracking record", "cycle tracking records"])}));
    return items;
  }
  const rangeText = r => r ? (r.from === r.to ? ` from ${day(r.from)}` : ` from ${day(r.from)} to ${day(r.to)}`) : "";
  const notes = j => (j.warnings || []).length ? node("details", {}, node("summary", {text: plural(j.warnings.length, ["note", "notes"]) + " from the import"}),
                                                 node("ul", {}, j.warnings.map(w => node("li", {text: w})))) : null;

  // lab names the preview asks about (#23): one select each, the suggestion (or the match made before) chosen first
  function labChoices(j) {
    const listed = j.labs || [];
    if (!listed.length) return {part: null, picked: () => ({})};
    const targets = j.lab_targets || [];
    const unitText = u => u ? ` (${u})` : "";
    const KEEP = "";   // no series has an empty name
    const rows = listed.map(l => {
      const sel = node("select", {"aria-label": `Series for ${l.name}`, "data-ref": l.ref});
      const before = l.before;
      const opts = [], seen = new Set();
      const add = (value, text, group) => { if (seen.has(value)) return; seen.add(value); (group || opts).push(node("option", {value, text})); };
      // first, so chosen unless changed: the match made before, else a suggestion whose unit (or code) agrees
      const sure = (l.suggestions || []).filter(s => s.sure), unsure = (l.suggestions || []).filter(s => !s.sure);
      if (before) add(before.target == null ? KEEP : before.target, before.target == null ? "Keep it as a series of its own (as before)" : `${before.target} (matched as before)`);
      for (const s of sure) add(s.name, `${s.name}${unitText(s.unit)}: ${s.reason}`);
      add(KEEP, "Keep it as a new series of its own");
      for (const s of unsure) add(s.name, `${s.name}: ${s.reason}`);
      const other = node("optgroup", {label: "Another series"}), more = [];
      for (const t of targets) add(t.name, t.name + unitText(t.unit), more);
      other.append(...more);
      sel.append(...opts);
      if (more.length) sel.append(other);
      const top = before ? null : sure[0] || unsure[0];
      const why = before ? node("span", {class: "before", text: before.target == null ? "Kept separate as before" : "Matched as before"})
                         : top ? node("span", {class: "why", text: `${top.sure ? "Suggested" : "Possibly"}: ${top.name}, ${top.reason}.`})
                         : node("span", {class: "why", text: "Nothing in this profile looks like it."});
      return {l, sel, initial: sel.value, li: node("li", {"data-ref": l.ref},
        node("span", {}, node("b", {text: l.name}), ` ${unitText(l.unit).trim()}`, l.loinc ? ` · LOINC ${l.loinc}` : "",
             ` · ${plural(l.n || 0, ["result", "results"])}`), why, sel)};
    });
    const part = node("details", {class: "imp-labs", open: ""},
      node("summary", {text: `${plural(listed.length, ["lab name", "lab names"])} to match`}),
      node("p", {class: "hint", text: "Sanguity doesn't know these test names. Put each in the series it belongs to, or keep it separate; " +
        "your choice is used for later imports from this source, on every profile of this server."}),
      more("In a series, a result is charted with the same test from other labs. Only the series changes: each result keeps the " +
        "name the lab gave it. A match made before can be changed here for this import; to change it for good, ask an admin (Admin, Lab name matches)."),
      node("ul", {}, rows.map(r => r.li)));
    // sent, and remembered for later imports: a suggestion left chosen, or any choice changed by hand. A name matched
    // before is sent only when changed (for this import only); "keep it separate" left as it was isn't sent
    const picked = () => Object.fromEntries(rows.filter(r => r.sel.value !== r.initial || (!r.l.before && r.initial !== KEEP))
      .map(r => [r.l.ref, r.sel.value === KEEP ? null : r.sel.value]));
    return {part, picked};
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
      node("p", {class: "hint", text: "Each of these has the same test, time and value as a result another source brought. " +
        "Tick the ones that are the same result to show them once; nothing is merged unless you tick it."}),
      more("Values are compared with their units converted. A result shown once lists both sources, and you can undo the link " +
        "later from the result's panel."),
      node("ul", {}, listed.map(x => {
        const b = node("input", {type: "checkbox", value: x.ref, "aria-label": `Same result: ${x.name} ${valueText(x)}`});
        boxes.push(b);
        return node("li", {"data-ref": x.ref}, node("label", {}, b, " Same result"),
          node("span", {}, node("b", {text: x.name}), ` ${valueText(x)} · ${when(x.t)} · from ${j.label || j.importer}`),
          node("span", {class: "why", text: `Possibly the same result as ${x.other.name} ${valueText(x.other)} · ${when(x.other.t)} · from ${x.other.label || "another import"}`}));
      })));
    return {part, picked: () => boxes.filter(b => b.checked).map(b => b.value)};
  }

  function showPreview(card, j, onSave, onCancel) {
    const d = j.duplicates || {items: []}, items = countItems(j.counts || {});
    const nDup = (d.events || 0) + (d.results || 0);
    const head = j.new
      ? node("p", {}, node("b", {text: "Ready to import."}), ` Read as ${j.label || j.importer}${rangeText(j.range)}. Nothing is saved until you choose Save.`)
      : node("p", {class: "imp-alldup"}, node("b", {text: "All duplicates."}),
             ` Everything in this upload (read as ${j.label || j.importer}${rangeText(j.range)}) is in this profile already, so saving it adds nothing.`);
    const boxes = [];
    const dupList = d.items && d.items.length ? node("details", {class: "imp-dups"},
      node("summary", {text: `${plural(nDup, ["duplicate", "duplicates"])} skipped: show them`}),
      node("p", {class: "hint", text: "These are in the profile already. Tick any you want saved again anyway."}),
      node("ul", {}, d.items.map(it => {
        const box = node("input", {type: "checkbox", value: it.ref});
        boxes.push(box);
        const what = it.kind === "results" ? plural(it.n || 0, ["lab result", "lab results"]) + ": " + it.title
                                           : (KIND[it.kind] || [it.kind])[0] + ": " + it.title;
        return node("li", {}, node("label", {}, box, " ", node("span", {class: "d", text: it.t ? day(it.t) : ""}), " ", what));
      })),
      d.more ? node("p", {class: "hint", text: `…and ${d.more.toLocaleString()} more.`}) : null)
      : nDup ? node("p", {text: `${plural(nDup, ["duplicate", "duplicates"])} skipped.`}) : null;
    const labs = labChoices(j), same = sameChoices(j);
    const msg = node("span", {class: "msg", role: "status"});
    const save = node("button", {class: "btn primary", type: "button", text: "Save"});
    const cancel = node("button", {class: "btn", type: "button", text: "Cancel"});
    const busy = on => { save.disabled = cancel.disabled = on; };
    save.addEventListener("click", async () => {
      busy(true); msg.textContent = "Saving…";
      if (!(await onSave(boxes.filter(b => b.checked).map(b => b.value), labs.picked(), same.picked()))) busy(false);
    });
    cancel.addEventListener("click", async () => {
      busy(true); msg.textContent = "Cancelling…";
      if (!(await onCancel())) busy(false);
    });
    fill(card, head, j.new && items.length ? node("ul", {}, items) : null, labs.part, same.part, dupList, notes(j),
         node("div", {class: "imp-actions"}, save, cancel, msg));
    return t => { msg.textContent = t; };
  }

  // replaceChildren turns a null into the text "null"; parts that aren't there are left out instead
  function fill(card, ...parts) { card.replaceChildren(...parts.filter(p => p != null)); }

  function showDone(card, pid, j) {
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
  }

  function showFailed(card, text) {
    // the message is plain text; any address in it (the issues page) becomes a link, nothing else is markup
    const parts = String(text || "The import failed; nothing was saved.").split(/(https:\/\/[^\s)]+[^\s).,])/);
    card.replaceChildren(node("p", {}, node("b", {text: "Not imported. "}),
      parts.map((p, i) => i % 2 ? node("a", {href: p, target: "_blank", rel: "noopener noreferrer", text: p}) : p)));
    card.classList.add("error");
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
        if (j.state === "preview") return previewed(card, pid, j);
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
    card.replaceChildren(node("p", {}, node("b", {text: "Cancelled."}), " Nothing was saved, and the upload was removed."));
  }

  // the preview's Save and Cancel; resolves with the job once it's saved (or failed) or cancelled
  function previewed(card, pid, j) {
    const base = `profiles/${encodeURIComponent(pid)}/jobs/${j.id}`;
    return new Promise(done => {
      let say = () => {};
      const act = async (what, body) => {
        let r;
        try { r = await call(`${base}/${what}`, {method: "POST", body: JSON.stringify(body || {})}); }
        catch (e) { say("Couldn't reach the server. Check the connection and try again."); return false; }
        if (r.status === 401) { location.replace("login.html"); return false; }
        if (!r.ok) { say(await detail(r)); return false; }
        return true;
      };
      say = showPreview(card, j,
        async (keep, labs, same) => {
          if (!(await act("confirm", {keep, labs, same}))) return false;
          const status = node("p", {text: STAGES.saving});
          card.replaceChildren(status);
          done(poll(card, pid, j.id, status));
          return true;
        },
        async () => {
          if (!(await act("discard"))) return false;
          showCancelled(card);
          done(j);
          return true;
        });
    });
  }

  // an import someone else started (a connected source's first pull, connect.js): its progress and preview here
  function follow(opts) {
    const card = box(opts && opts.container), status = node("p", {text: "Reading the records�"});
    card.replaceChildren(status);
    return poll(card, opts.profileId, opts.jobId, status);
  }

  window.sanguityImport = {uploaded, follow};
})();
