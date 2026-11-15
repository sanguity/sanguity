/* Sanguity · importing an upload in the background (#20). The import page (#19) calls
     window.sanguityImport.uploaded({profileId, uploadId[, container][, importer]})
   once an upload is finished. This starts the import, polls it (about once a second, slower the longer it runs),
   shows the stage, and then what came in, with a link to the timeline, or why nothing did.
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

  function showDone(card, pid, j) {
    const c = j.counts || {}, kinds = Object.entries(c.kinds || {}).sort((a, b) => b[1] - a[1]);
    const items = kinds.filter(([k]) => k !== "labitem").map(([k, n]) => node("li", {text: plural(n, KIND[k] || [k, k])}));
    if (c.results) items.push(node("li", {text: plural(c.results, ["lab result", "lab results"]) + " in " + plural(c.series || 0, ["test", "tests"])}));
    if (c.documents) items.push(node("li", {text: plural(c.documents, ["attached file", "attached files"])}));
    const range = j.range ? (j.range.from === j.range.to ? ` from ${day(j.range.from)}` : ` from ${day(j.range.from)} to ${day(j.range.to)}`) : "";
    card.replaceChildren(
      node("p", {}, node("b", {text: "Imported."}), ` Read as ${j.label || j.importer}${range}.`),
      items.length ? node("ul", {}, items) : node("p", {text: "The files held no records to show."}),
      (j.warnings || []).length ? node("details", {}, node("summary", {text: plural(j.warnings.length, ["note", "notes"]) + " from the import"}),
                                       node("ul", {}, j.warnings.map(w => node("li", {text: w})))) : null,
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
    const jid = (await r.json()).id, began = Date.now();
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
        const secs = Math.round((Date.now() - began) / 1000);
        status.textContent = (STAGES[j.state === "queued" ? "queued" : j.stage] || "Working…") + (secs >= 5 ? ` (${secs} s)` : "");
      }
      // about once a second at first; slower for a long import, and slower still while the server can't be reached
      const elapsed = Date.now() - began;
      wait = misses ? Math.min(10000, 1000 * 2 ** misses) : elapsed < 20000 ? 1000 : elapsed < 120000 ? 2000 : 5000;
    }
  }

  window.sanguityImport = {uploaded};
})();
