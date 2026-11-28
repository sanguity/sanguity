/* Sanguity · Sources → Portals and health plans (#32, #91, #166): a profile's connections (sign in to a source, its
   first pull previewed like an upload, then synced on a schedule; Sync now, Reconnect when access ended, Disconnect
   keeping or removing the records). Setting each source up (this server's own app with each vendor) is on the Admin
   page (admin-sources.js). What each connection pulls is chosen when connecting and changed later (#99: kinds of
   records, a start date, removing what a kind brought), with its attachment rules and the attachments left out
   (#101), and a warning when it stopped syncing (#102). window.sanguityImport.follow (import-jobs.js) shows the first pull's preview.
   The Sources page signs in and chooses the profile (sources-page.js). window.SgConnect tells providers.js, which
   finds your provider in the connect step, the sources this server has set up, and opens the connect form for one;
   a "connect:sources" event on document says they're read again. */
"use strict";   // $, el and api come from util.js
(() => {
  async function call(path, method = "GET", body, opts = {}) {
    const r = await api(path, {method, body: body ? JSON.stringify(body) : undefined, ...opts});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
    return j;
  }
  const when = fmtWhen;   // dates.js
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const size = b => b == null ? "size unknown" : b < 1024 ? `${b} bytes` : b < 2 ** 20 ? `${Math.round(b / 1024)} KB` : `${(b / 2 ** 20).toFixed(1)} MB`;
  const ATT_TYPES = {pdf: "PDF", image: "Images", text: "Text", html: "Web pages (HTML)"};
  const WHY = {ask: "fetched only when you ask", type: "a kind of file this connection doesn't fetch",
    size: "bigger than this connection's limit", budget: "over this connection's total for attachments"};
  const today = () => new Date().toISOString().slice(0, 10);
  const params = new URLSearchParams(location.search);
  let ME, PID = null, followed = new Set(), timer = null, hashShown = false;

  if (params.get("problem")) { $("#problem").textContent = params.get("problem"); $("#problem").hidden = false; }
  if (params.has("problem") || params.has("connected")) try { history.replaceState(null, "", location.pathname + (params.get("profile") ? "?profile=" + encodeURIComponent(params.get("profile")) : "") + location.hash); } catch (e) {}

  /* ---------------------------------------------------------------- a profile's connections */
  const PHASE = {
    "first pull": "Pulling your records for the first time…",
    "first pull preview": "The first pull is ready: look at its preview below and save it.",
    "first pull saving": "Saving the first pull…",
    "first pull failed": "The first pull failed. Pull again to retry.",
    "first pull discarded": "The first pull was cancelled. Pull again to bring the records in.",
    "syncing": "Connected. New records come in on a schedule.",
    "needs reconnect": "Access ended: sign in again to keep this source up to date.",
    "disconnected": "Disconnected.",
  };

  function reportLine(rep) {
    if (!rep || !rep.types) return null;
    const parts = Object.entries(rep.types).map(([t, x]) => x.status === "ok" ? `${t} ${x.n}` : `${t}: ${x.status}`);
    return el("p", {class: "muted", text: "Last pull, by kind: " + parts.join(" · ")});
  }

  /* the kinds of records, as checkboxes: those the access allows, and (for a connection) those it would need signing
     in again for; returns {node, get(): [ids]} */
  function kindPicker(kinds, chosen, name, reconnectNote) {
    const box = el("fieldset", {class: "conn-kinds"}, el("legend", {text: "What to pull"}));
    for (const k of kinds) {
      const input = el("input", {type: "checkbox", name, value: k.id, checked: chosen.includes(k.id) ? "" : null,
        disabled: !k.allowed && !reconnectNote ? "" : null});
      box.append(el("label", {class: "check conn-kind"}, input, el("span", {},
        el("b", {text: k.label}), " ", el("span", {class: "muted", text: k.brings +
          (k.allowed ? "" : reconnectNote ? " " + reconnectNote : " This source's setup doesn't ask for it.")}))));
    }
    return {node: box, get: () => [...box.querySelectorAll("input:checked")].map(i => i.value)};
  }

  function sincePicker(value, id) {
    const input = el("input", {type: "date", id, max: today(), value: value || ""});
    return {node: el("label", {}, "From (leave empty for everything) ", input), get: () => input.value || null};
  }

  /* "Connect X…": choose what to pull, then on to the vendor's sign-in with only the scopes that needs */
  function connectForm(s) {
    const all = s.kinds.filter(k => k.allowed).map(k => k.id);
    const kinds = kindPicker(s.kinds, all, "k-new-" + s.id), since = sincePicker(null, "since-new-" + s.id);
    const msg = el("span", {class: "msg", role: "status"});
    const go = el("button", {class: "btn primary", type: "button", text: `Sign in at ${s.host}`, onclick: async () => {
      sayIn(msg, "");
      const chosen = kinds.get();
      if (!chosen.length) { sayIn(msg, "Choose at least one kind of record.", true); return; }
      try {
        const r = await call(`profiles/${PID}/connect/start`, "POST",
          {source: s.id, kinds: chosen.length === all.length ? null : chosen, since: since.get()});
        location.assign(r.url);
      } catch (e) { sayIn(msg, e.message, true); }
    }});
    return el("div", {class: "conn-choose", id: "choose-" + s.id},
      el("p", {class: "hint", text: "Sanguity asks the vendor only for what you choose here, and you can change it later."}),
      kinds.node, since.node,
      el("div", {class: "row"}, go, el("button", {class: "btn", type: "button", text: "Cancel", onclick: ev => ev.target.closest(".conn-choose").remove()}), msg));
  }

  function chosenLine(c) {
    const names = c.kinds.filter(k => c.chosen.includes(k.id)).map(k => k.label);
    return `Pulls ${c.all_kinds ? "everything it's allowed" : names.join(", ").toLowerCase() || "nothing"}` +
      (c.all_kinds && names.length ? ` (${names.join(", ").toLowerCase()})` : "") + (c.since ? `, from ${fmtD(c.since)}` : "") +
      `. Attachments: ${c.attach.auto ? `fetched automatically (${c.attach.types.map(t => ATT_TYPES[t]).join(", ") || "none"}, up to ${c.attach.max_mb} MB each)` : "only when you ask"}.`;
  }

  /* changing what a connection pulls and its attachment rules; kinds turned off can have what they brought removed */
  function optionsForm(c, box) {
    const kinds = kindPicker(c.kinds, c.chosen, "k-" + c.id, "Needs signing in again.");
    const since = sincePicker(c.since, "since-" + c.id);
    const own = c.attach_own || {}, a = c.attach;
    const auto = el("input", {type: "checkbox", role: "switch", checked: a.auto ? "" : null});   // a switch (#259)
    const types = Object.entries(ATT_TYPES).map(([id, label]) => el("label", {class: "check"},
      el("input", {type: "checkbox", value: id, checked: a.types.includes(id) ? "" : null}), label));
    const num = (v, max, label) => el("input", {type: "number", min: 1, max, step: 1, value: v, "aria-label": label, class: "conn-num"});
    const maxMb = num(a.max_mb, a.ceiling_mb, "Largest attachment, MB"), budget = num(a.budget_mb, 1048576, "Total for attachments, MB");
    const msg = el("span", {class: "msg", role: "status"});
    const removeRow = el("div", {class: "conn-remove"});
    const drawRemove = chosen => {
      const off = c.kinds.filter(k => !chosen.includes(k.id) && (k.allowed || c.chosen.includes(k.id)));   // #228: not one it never could pull
      removeRow.replaceChildren(...(off.length && c.imports ? [el("p", {class: "hint", text: "Kinds turned off aren't pulled any more; what they brought stays. To take it out (you can put it back from the import history):"}),
        el("div", {class: "row"}, ...off.map(k => el("button", {class: "btn small", type: "button", text: `Remove ${k.label.toLowerCase()} it brought`,
          onclick: async ev => {
            ev.target.disabled = true;
            try {
              const r = await call(`profiles/${PID}/connections/${c.id}/remove`, "POST", {kind: k.id});
              load(`Removed the ${k.label.toLowerCase()} ${c.source} brought. Undo it from the import history to put them back.`);
            } catch (e) { sayIn(msg, e.message, true); ev.target.disabled = false; }
          }})))] : []));
    };
    drawRemove(c.chosen);
    const save = el("button", {class: "btn small primary", type: "button", text: "Save", onclick: async () => {
      sayIn(msg, "");
      const chosen = kinds.get();
      if (!chosen.length) { sayIn(msg, "Choose at least one kind of record.", true); return; }
      const attach = {auto: auto.checked, types: types.map(l => l.querySelector("input")).filter(i => i.checked).map(i => i.value),
        max_mb: +maxMb.value, budget_mb: +budget.value};
      try {
        const needSignIn = c.kinds.some(k => chosen.includes(k.id) && !k.allowed);
        await call(`profiles/${PID}/connections/${c.id}`, "PATCH", {attach, since: since.get(),
          ...(needSignIn ? {} : {kinds: chosen})});
        if (needSignIn) {   // a kind the access doesn't cover: sign in again, asking for it too
          location.assign((await call(`profiles/${PID}/connections/${c.id}/reconnect`, "POST", {kinds: chosen, since: since.get()})).url);
          return;
        }
        load("Saved. The next sync pulls by the new choice.");
      } catch (e) { sayIn(msg, e.message, true); }
    }});
    kinds.node.addEventListener("change", () => drawRemove(kinds.get()));
    return el("div", {class: "conn-options"},
      kinds.node, since.node,
      el("fieldset", {class: "conn-att"}, el("legend", {text: "Attachments"}),
        el("label", {class: "check switch"}, auto, "Fetch automatically (off: only when you ask, from the list below)"),
        el("div", {class: "row conn-types"}, ...types),
        el("label", {}, "Largest, in MB ", maxMb, el("span", {class: "muted", text: ` (this server allows up to ${a.ceiling_mb})`})),
        el("label", {}, "In all, for this connection, in MB ", budget, el("span", {class: "muted", text: ` (fetched so far: ${size(c.attach_bytes)})`})),
        Object.keys(own).length ? el("button", {class: "btn small", type: "button", text: "Use this server's defaults", onclick: async () => {
          try { await call(`profiles/${PID}/connections/${c.id}`, "PATCH", {attach: {auto: null, types: null, max_mb: null, budget_mb: null}}); load("Back to this server's attachment defaults."); }
          catch (e) { sayIn(msg, e.message, true); } }}) : null),
      removeRow,
      el("div", {class: "row"}, save, el("button", {class: "btn small", type: "button", text: "Cancel", onclick: () => box.querySelector(".conn-options").remove()}), msg));
  }

  function skippedList(c, canEdit) {
    if (!c.skipped.length) return null;
    return el("details", {class: "conn-skipped"}, el("summary", {text: `Attachments left out (${c.skipped.length})`}),
      el("ul", {}, ...c.skipped.map(x => {
        const msg = el("span", {class: "msg", role: "status"});
        return el("li", {}, el("span", {text: `${x.title} · ${ATT_TYPES[x.type] || x.content_type || "unknown kind"} · ${size(x.size)} · ${WHY[x.why] || x.why}`}), " ",
          canEdit && c.state === "active" ? el("button", {class: "btn small", type: "button", text: "Fetch anyway", onclick: async ev => {
            ev.target.disabled = true; sayIn(msg, "Fetching…");
            try { const r = await call(`profiles/${PID}/connections/${c.id}/attachments/${x.id}/fetch`, "POST"); load(`Fetched “${x.title}” (${size(r.size)}).`); }
            catch (e) { sayIn(msg, e.message, true); ev.target.disabled = false; } }}) : null, msg);
      })));
  }

  function card(c, canEdit) {
    const box = el("div", {class: "prof conn-item" + (c.state === "needs reconnect" || c.warning ? " conn-bad" : ""), id: "conn-" + c.id});
    box.append(el("b", {text: c.source}), el("span", {class: "tag", text: c.host}));
    box.append(el("p", {text: PHASE[c.phase] || c.phase}));
    if (c.warning && c.warning.kind !== "access") box.append(el("p", {class: "msg bad conn-warning", text: c.warning.text}));
    if (c.error && c.state === "active") {
      box.append(el("p", {class: c.error_kind === "unavailable" ? "muted" : "msg bad",
        text: (c.error_kind === "unavailable" ? "Vendor unavailable: " : "Last pull failed: ") + c.error}));
    } else if (c.error && c.state === "needs reconnect") box.append(el("p", {class: "msg bad", text: c.error}));
    if (c.state !== "disconnected") {
      box.append(el("p", {class: "muted", text: `Last pull ${when(c.last_sync)}` + (c.next_sync && c.state === "active" ? ` · next ${when(c.next_sync)}` : "")
        + ` · ${plural(c.imports, "import")} in the history` + (c.credential === "access" ? " · no refresh token: sign in again when access runs out" : "")}));
      box.append(el("p", {class: "muted conn-chosen", text: chosenLine(c)}));
      const rep = reportLine(c.report);
      if (rep) box.append(rep);
      for (const w of (c.report && c.report.warnings) || []) box.append(el("p", {class: "muted", text: w}));
      const sk = skippedList(c, canEdit);
      if (sk) box.append(sk);
    } else box.append(el("p", {class: "muted", text: `Disconnected ${when(c.disconnected)}`}));
    if (!canEdit || c.state === "disconnected") return box;
    const row = el("div", {class: "row"}), msg = el("span", {class: "msg", role: "status"});
    const act = (label, fn, primary) => el("button", {class: "btn small" + (primary ? " primary" : ""), type: "button", text: label,
      onclick: async () => { sayIn(msg, ""); try { await fn(); } catch (e) { sayIn(msg, e.message, true); } }});
    if (c.state === "needs reconnect") row.append(act("Sign in again", async () => {
      location.assign((await call(`profiles/${PID}/connections/${c.id}/reconnect`, "POST")).url); }, true));
    if (c.state === "active" && !["first pull preview", "first pull saving"].includes(c.phase))
      row.append(act(c.phase.startsWith("first pull ") ? "Pull again" : "Sync now", async () => {
        await call(`profiles/${PID}/connections/${c.id}/sync`, "POST"); sayIn(msg, "Pulling…"); load(); }));
    row.append(act("What it pulls…", async () => {
      if (box.querySelector(".conn-options")) return;
      box.insertBefore(optionsForm(c, box), row.nextSibling);
    }));
    row.append(act("Disconnect…", async () => {
      const form = el("div", {class: "conn-disc"},
        el("p", {text: "Disconnecting revokes Sanguity's access where the vendor allows it and deletes the stored tokens. The records it brought:"}),
        el("label", {class: "check"}, el("input", {type: "radio", name: "keep" + c.id, value: "keep", checked: ""}), "Keep them"),
        el("label", {class: "check"}, el("input", {type: "radio", name: "keep" + c.id, value: "remove"}), `Remove them (${plural(c.imports, "import")}, undone from the history)`),
        // its first pull, not saved yet, is cancelled either way (#592)
        ["first pull", "first pull preview", "first pull saving"].includes(c.phase)
          ? el("p", {class: "muted", text: "Its first pull isn't saved yet: it's cancelled either way, and nothing it read is kept."}) : null);
      const go = act("Disconnect", async () => {
        const remove = form.querySelector("input[value=remove]").checked;
        const r = await call(`profiles/${PID}/connections/${c.id}/disconnect`, "POST", {remove_records: remove});
        $("#problem").hidden = true;
        load(r.revoked === false ? "Disconnected. The vendor didn't confirm the revocation; you can also end access in your portal." : null);
      }, true);
      form.append(el("div", {class: "row"}, go, el("button", {class: "btn small", type: "button", text: "Cancel", onclick: () => form.remove()})));
      box.append(form);
    }));
    row.append(msg);
    box.append(row);
    return box;
  }

  async function load(note, polled = false) {   // polled: the page's own refresh, not use (#1063)
    clearTimeout(timer);
    if (!PID) return;
    let d;
    const pid = PID;
    try { d = await call(`profiles/${pid}/connections`, "GET", undefined, polled ? background() : {}); }
    catch (e) { if (pid === PID) $("#connList").replaceChildren(el("p", {class: "msg bad", role: "alert", text: e.message})); return; }
    if (pid !== PID) return;   // another profile was picked meanwhile: its own load draws it
    const live = d.connections.filter(c => c.state !== "disconnected"), gone = d.connections.filter(c => c.state === "disconnected");
    const list = [...live.map(c => card(c, d.can_connect))];
    if (!live.length) list.push(el("p", {class: "muted", text: "This profile has no connected sources."}));
    if (gone.length) list.push(el("details", {}, el("summary", {text: `Disconnected (${gone.length})`}), ...gone.map(c => card(c, false))));
    if (note) { const m = el("p", {class: "msg"}); sayIn(m, note); list.unshift(m); }
    $("#connList").replaceChildren(...list);
    const ended = live.filter(c => c.state === "needs reconnect");
    $("#reconnect").hidden = !ended.length;
    $("#reconnect").textContent = ended.length ? `${ended.map(c => c.source).join(", ")}: access ended. Sign in again below to keep the records up to date.` : "";
    if (params.get("connection")) { const t = document.getElementById("conn-" + params.get("connection")); if (t) t.scrollIntoView({block: "center"}); params.delete("connection"); }
    if (!hashShown && /^#conn-\d+$/.test(location.hash)) {   // sources.html#conn-3: the main page's warning (#102), the overview
      hashShown = true;
      const t = document.getElementById(location.hash.slice(1)); if (t) t.scrollIntoView({block: "center"});
    }
    $("#viewerNote").hidden = d.can_connect;
    $("#noSources").hidden = !!d.sources.length || !d.can_connect;
    $("#adminNote").hidden = !ME.user.is_admin || !$("#noSources").hidden;   // (none set up: the line above says it, #280)
    $("#sourceButtons").replaceChildren(...(d.can_connect ? d.sources : []).filter(s => !s.waiting).map(s =>
      el("button", {class: "btn", type: "button", text: `Connect ${s.name}…`, "data-tip": `Signs in at ${s.host}`, onclick: () => {
        const open = document.getElementById("choose-" + s.id);
        if (open) { open.remove(); return; }
        $("#sourceButtons").after(connectForm(s));
      }})));
    // waiting for the vendor's review (#165): what's coming, and what to do meanwhile, in place of a connect button
    $("#sourceWaiting").replaceChildren(...(d.can_connect ? d.sources : []).filter(s => s.waiting).map(s =>
      el("div", {class: "conn-wait", id: "wait-" + s.id}, el("p", {}, el("b", {text: s.name}), " ", el("span", {class: "tag", text: "Not ready yet"})),
        el("p", {text: s.note}),
        el("div", {class: "row"}, el("a", {class: "btn small", href: "#upload", text: "Upload files"})))));
    SgConnect.sources = d.sources; SgConnect.canConnect = d.can_connect;
    document.dispatchEvent(new CustomEvent("connect:sources"));
    for (const c of live) {   // the first pull's preview, as on the import page
      if (c.first_job && ["queued", "running", "preview"].includes(c.first_job_state) && !followed.has(c.first_job) && d.can_connect) {
        followed.add(c.first_job);
        window.sanguityImport.follow({profileId: PID, jobId: c.first_job, container: "#import-status", title: `${c.source}: the first records`}).then(() => load(undefined, true));
      }
    }
    if (live.some(c => c.phase === "first pull" || c.phase === "first pull saving")) timer = setTimeout(() => load(undefined, true), 2000);
  }

  /* the connect step of finding your provider (providers.js): the sources set up here, and connecting one */
  window.SgConnect = {sources: [], canConnect: false, connect(id) {
    const s = SgConnect.sources.find(x => x.id === id);
    if (!s || s.waiting || !SgConnect.canConnect) return;
    const open = document.getElementById("choose-" + s.id) || connectForm(s);
    $("#sourceButtons").after(open);
    open.scrollIntoView({block: "nearest"});
    open.querySelector("input")?.focus();
  }};

  /* ---------------------------------------------------------------- start */
  (async function () {
    ME = await SgSources.ready;   // the page signs in and chooses the profile (sources-page.js)
    if (!sgConnected(ME)) return;   // (#1096) off: the section stays hidden, nothing asked
    if (ME.user.is_admin) $("#noSources").replaceChildren("No portal or health plan is set up on this server yet: as an admin, you set them up under ",
      el("a", {href: "settings.html#admin", text: "Settings → Connected sources"}), ".");
    PID = SgSources.pid();
    $("#profile").addEventListener("change", () => { PID = SgSources.pid(); load(); });
    $("#mine").hidden = false;
    load(params.has("connected") ? "Connected. The first pull starts now; its preview shows here when it's ready." : null);
  })();
})();
