/* Sanguity · importing an upload in the background (#20). The import page (#19) calls
     window.sanguityImport.uploaded({profileId, uploadId[, container][, importer]})
   once an upload is finished. This starts the import, polls it (about once a second, slower the longer it runs),
   shows the stage, then its preview (#22): what it would add by kind, the dates it covers, and the duplicates of
   what the profile has, skipped unless ticked to keep, with Save and Cancel. Saving polls again and shows what came
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
                exercise: ["workout", "workouts"]};

  function node(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "text") e.textContent = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
    }
    for (const c of kids.flat()) if (c != null) e.append(c);
    return e;
  }
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
  const day = s => { const d = new Date(s + "T12:00:00"); return isNaN(d) ? s : d.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"}); };

  function box(container) {
    let host = container;
    if (typeof host === "string") host = document.querySelector(host);
    if (!host) host = document.querySelector("#import-status");
    if (!host) { host = node("section", {id: "import-status"}); (document.querySelector("main") || document.body).append(host); }
    const card = node("div", {class: "card import-job", role: "status", "aria-live": "polite"});
    host.append(card);
    return card;
  }

  function openTimeline(pid) {
    // index.html opens the profile and tab it last saved for this browser (core.js: ht-profile, ht-state:<id>)
    try {
      localStorage.setItem("ht-profile", String(pid));
      const k = "ht-state:" + pid, s = JSON.parse(localStorage.getItem(k) || "{}") || {};
      localStorage.setItem(k, JSON.stringify({...s, tab: "timeline", v: 2}));
    } catch (e) {}
  }

  function countItems(c) {
    const kinds = Object.entries(c.kinds || {}).sort((a, b) => b[1] - a[1]);
    const items = kinds.filter(([k]) => k !== "labitem").map(([k, n]) => node("li", {text: plural(n, KIND[k] || [k, k])}));
    if (c.results) items.push(node("li", {text: plural(c.results, ["lab result", "lab results"]) + " in " + plural(c.series || 0, ["test", "tests"])}));
    if (c.documents) items.push(node("li", {text: plural(c.documents, ["attached file", "attached files"])}));
    if (c.watch_days) items.push(node("li", {text: plural(c.watch_days, ["day of activity data", "days of activity data"])}));
    if (c.workouts && !(c.kinds || {}).exercise) items.push(node("li", {text: plural(c.workouts, KIND.exercise)}));
    return items;
  }
  const rangeText = r => r ? (r.from === r.to ? ` from ${day(r.from)}` : ` from ${day(r.from)} to ${day(r.to)}`) : "";
  const notes = j => (j.warnings || []).length ? node("details", {}, node("summary", {text: plural(j.warnings.length, ["note", "notes"]) + " from the import"}),
                                                 node("ul", {}, j.warnings.map(w => node("li", {text: w})))) : null;

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
    const msg = node("span", {class: "msg", role: "status"});
    const save = node("button", {class: "btn primary", type: "button", text: "Save"});
    const cancel = node("button", {class: "btn", type: "button", text: "Cancel"});
    const busy = on => { save.disabled = cancel.disabled = on; };
    save.addEventListener("click", async () => {
      busy(true); msg.textContent = "Saving…";
      if (!(await onSave(boxes.filter(b => b.checked).map(b => b.value)))) busy(false);
    });
    cancel.addEventListener("click", async () => {
      busy(true); msg.textContent = "Cancelling…";
      if (!(await onCancel())) busy(false);
    });
    card.replaceChildren(head, j.new && items.length ? node("ul", {}, items) : null, dupList, notes(j),
                         node("div", {class: "imp-actions"}, save, cancel, msg));
    return t => { msg.textContent = t; };
  }

  function showDone(card, pid, j) {
    const c = j.counts || {}, items = countItems(c), sk = j.skipped || {};
    const skipped = (sk.events || 0) + (sk.results || 0);
    card.replaceChildren(
      node("p", {}, node("b", {text: "Imported."}), ` Read as ${j.label || j.importer}${rangeText(j.range)}.`),
      items.length ? node("ul", {}, items) : node("p", {text: skipped ? "Nothing new: it was all in the profile already." : "The files held no records to show."}),
      skipped ? node("p", {class: "hint", text: `${plural(skipped, ["duplicate", "duplicates"])} skipped.`}) : null,
      notes(j),
      node("p", {}, node("a", {href: "index.html", onclick: () => openTimeline(pid), text: "See them on the timeline"})));
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
        async keep => {
          if (!(await act("confirm", {keep}))) return false;
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

  window.sanguityImport = {uploaded};
})();
