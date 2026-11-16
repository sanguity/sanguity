/* Sanguity · connected sources (#32, #91): a profile's connections (sign in to a source, its first pull previewed like
   an upload, then synced on a schedule; Sync now, Reconnect when access ended, Disconnect keeping or removing the
   records), and for admins the guided setup of each source (option 1: this install's own app with each vendor).
   What each connection pulls is chosen when connecting and changed later (#99: kinds of records, a start date,
   removing what a kind brought), with its attachment rules and the attachments left out (#101), and a warning when
   it stopped syncing (#102). window.sanguityImport.follow (import-jobs.js) shows the first pull's preview. */
"use strict";   // $, el and api come from util.js
async function call(path, method = "GET", body) {
  const r = await api(path, {method, body: body ? JSON.stringify(body) : undefined});
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
let ME, PID = null, followed = new Set(), timer = null, SETUP = null, editing = null;

$("#logout").onclick = async () => { try { await call("logout", "POST"); } catch (e) {} location.replace("login.html"); };
if (params.get("problem")) { $("#problem").textContent = params.get("problem"); $("#problem").hidden = false; }
if (params.has("problem") || params.has("connected")) try { history.replaceState(null, "", location.pathname + (params.get("profile") ? "?profile=" + encodeURIComponent(params.get("profile")) : "")); } catch (e) {}

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
    msg.textContent = ""; msg.classList.remove("bad");
    const chosen = kinds.get();
    if (!chosen.length) { msg.textContent = "Choose at least one kind of record."; msg.classList.add("bad"); return; }
    try {
      const r = await call(`profiles/${PID}/connect/start`, "POST",
        {source: s.id, kinds: chosen.length === all.length ? null : chosen, since: since.get()});
      location.assign(r.url);
    } catch (e) { msg.textContent = e.message; msg.classList.add("bad"); }
  }});
  return el("div", {class: "conn-choose", id: "choose-" + s.id},
    el("p", {class: "hint", text: "Sanguity asks the vendor only for what you choose here, and you can change it later."}),
    kinds.node, since.node,
    el("div", {class: "row"}, go, el("button", {class: "btn", type: "button", text: "Cancel", onclick: ev => ev.target.closest(".conn-choose").remove()}), msg));
}

function chosenLine(c) {
  const names = c.kinds.filter(k => c.chosen.includes(k.id)).map(k => k.label);
  return `Pulls ${c.all_kinds ? "everything it's allowed" : names.join(", ").toLowerCase() || "nothing"}` +
    (c.all_kinds && names.length ? ` (${names.join(", ").toLowerCase()})` : "") + (c.since ? `, from ${c.since}` : "") +
    `. Attachments: ${c.attach.auto ? `fetched automatically (${c.attach.types.map(t => ATT_TYPES[t]).join(", ") || "none"}, up to ${c.attach.max_mb} MB each)` : "only when you ask"}.`;
}

/* changing what a connection pulls and its attachment rules; kinds turned off can have what they brought removed */
function optionsForm(c, box) {
  const kinds = kindPicker(c.kinds, c.chosen, "k-" + c.id, "Needs signing in again.");
  const since = sincePicker(c.since, "since-" + c.id);
  const own = c.attach_own || {}, a = c.attach;
  const auto = el("input", {type: "checkbox", checked: a.auto ? "" : null});
  const types = Object.entries(ATT_TYPES).map(([id, label]) => el("label", {class: "check"},
    el("input", {type: "checkbox", value: id, checked: a.types.includes(id) ? "" : null}), label));
  const num = (v, max, label) => el("input", {type: "number", min: 1, max, step: 1, value: v, "aria-label": label, class: "conn-num"});
  const maxMb = num(a.max_mb, a.ceiling_mb, "Largest attachment, MB"), budget = num(a.budget_mb, 1048576, "Total for attachments, MB");
  const msg = el("span", {class: "msg", role: "status"});
  const removeRow = el("div", {class: "conn-remove"});
  const drawRemove = chosen => {
    const off = c.kinds.filter(k => !chosen.includes(k.id));
    removeRow.replaceChildren(...(off.length && c.imports ? [el("p", {class: "hint", text: "Kinds turned off aren't pulled any more; what they brought stays. To take it out (you can put it back from the import history):"}),
      el("div", {class: "row"}, ...off.map(k => el("button", {class: "btn small", type: "button", text: `Remove ${k.label.toLowerCase()} it brought`,
        onclick: async ev => {
          ev.target.disabled = true;
          try {
            const r = await call(`profiles/${PID}/connections/${c.id}/remove`, "POST", {kind: k.id});
            load(`Removed the ${k.label.toLowerCase()} ${c.source} brought. Undo it from the import history to put them back.`);
          } catch (e) { msg.textContent = e.message; msg.classList.add("bad"); ev.target.disabled = false; }
        }})))] : []));
  };
  drawRemove(c.chosen);
  const save = el("button", {class: "btn small primary", type: "button", text: "Save", onclick: async () => {
    msg.textContent = ""; msg.classList.remove("bad");
    const chosen = kinds.get();
    if (!chosen.length) { msg.textContent = "Choose at least one kind of record."; msg.classList.add("bad"); return; }
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
    } catch (e) { msg.textContent = e.message; msg.classList.add("bad"); }
  }});
  kinds.node.addEventListener("change", () => drawRemove(kinds.get()));
  return el("div", {class: "conn-options"},
    kinds.node, since.node,
    el("fieldset", {class: "conn-att"}, el("legend", {text: "Attachments"}),
      el("label", {class: "check"}, auto, "Fetch automatically (off: only when you ask, from the list below)"),
      el("div", {class: "row conn-types"}, ...types),
      el("label", {}, "Largest, in MB ", maxMb, el("span", {class: "muted", text: ` (this server allows up to ${a.ceiling_mb})`})),
      el("label", {}, "In all, for this connection, in MB ", budget, el("span", {class: "muted", text: ` (fetched so far: ${size(c.attach_bytes)})`})),
      Object.keys(own).length ? el("button", {class: "btn small", type: "button", text: "Use this server's defaults", onclick: async () => {
        try { await call(`profiles/${PID}/connections/${c.id}`, "PATCH", {attach: {auto: null, types: null, max_mb: null, budget_mb: null}}); load("Back to this server's attachment defaults."); }
        catch (e) { msg.textContent = e.message; msg.classList.add("bad"); } }}) : null),
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
          ev.target.disabled = true; msg.textContent = "Fetching…"; msg.classList.remove("bad");
          try { const r = await call(`profiles/${PID}/connections/${c.id}/attachments/${x.id}/fetch`, "POST"); load(`Fetched “${x.title}” (${size(r.size)}).`); }
          catch (e) { msg.textContent = e.message; msg.classList.add("bad"); ev.target.disabled = false; } }}) : null, msg);
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
    onclick: async () => { msg.textContent = ""; msg.classList.remove("bad"); try { await fn(); } catch (e) { msg.textContent = e.message; msg.classList.add("bad"); } }});
  if (c.state === "needs reconnect") row.append(act("Sign in again", async () => {
    location.assign((await call(`profiles/${PID}/connections/${c.id}/reconnect`, "POST")).url); }, true));
  if (c.state === "active" && !["first pull preview", "first pull saving"].includes(c.phase))
    row.append(act(c.phase.startsWith("first pull ") ? "Pull again" : "Sync now", async () => {
      await call(`profiles/${PID}/connections/${c.id}/sync`, "POST"); msg.textContent = "Pulling…"; load(); }));
  row.append(act("What it pulls…", async () => {
    if (box.querySelector(".conn-options")) return;
    box.insertBefore(optionsForm(c, box), row.nextSibling);
  }));
  row.append(act("Disconnect…", async () => {
    const form = el("div", {class: "conn-disc"},
      el("p", {text: "Disconnecting revokes Sanguity's access where the vendor allows it and deletes the stored tokens. The records it brought:"}),
      el("label", {class: "check"}, el("input", {type: "radio", name: "keep" + c.id, value: "keep", checked: ""}), "Keep them"),
      el("label", {class: "check"}, el("input", {type: "radio", name: "keep" + c.id, value: "remove"}), `Remove them (${plural(c.imports, "import")}, undone from the history)`));
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

async function load(note) {
  clearTimeout(timer);
  if (!PID) return;
  let d;
  try { d = await call(`profiles/${PID}/connections`); }
  catch (e) { $("#connList").replaceChildren(el("p", {class: "msg bad", text: e.message})); return; }
  const live = d.connections.filter(c => c.state !== "disconnected"), gone = d.connections.filter(c => c.state === "disconnected");
  const list = [...live.map(c => card(c, d.can_connect))];
  if (!live.length) list.push(el("p", {class: "muted", text: "This profile has no connected sources."}));
  if (gone.length) list.push(el("details", {}, el("summary", {text: `Disconnected (${gone.length})`}), ...gone.map(c => card(c, false))));
  if (note) list.unshift(el("p", {class: "msg", text: note}));
  $("#connList").replaceChildren(...list);
  const ended = live.filter(c => c.state === "needs reconnect");
  $("#reconnect").hidden = !ended.length;
  $("#reconnect").textContent = ended.length ? `${ended.map(c => c.source).join(", ")}: access ended. Sign in again below to keep the records up to date.` : "";
  if (params.get("connection")) { const t = document.getElementById("conn-" + params.get("connection")); if (t) t.scrollIntoView({block: "center"}); params.delete("connection"); }
  $("#viewerNote").hidden = d.can_connect;
  $("#noSources").hidden = !!d.sources.length || !d.can_connect;
  $("#sourceButtons").replaceChildren(...(d.can_connect ? d.sources : []).map(s =>
    el("button", {class: "btn", type: "button", text: `Connect ${s.name}…`, title: `Signs in at ${s.host}`, onclick: () => {
      const open = document.getElementById("choose-" + s.id);
      if (open) { open.remove(); return; }
      $("#sourceButtons").after(connectForm(s));
    }})));
  for (const c of live) {   // the first pull's preview, as on the import page
    if (c.first_job && ["queued", "running", "preview"].includes(c.first_job_state) && !followed.has(c.first_job) && d.can_connect) {
      followed.add(c.first_job);
      window.sanguityImport.follow({profileId: PID, jobId: c.first_job, container: "#import-status"}).then(() => load());
    }
  }
  if (live.some(c => c.phase === "first pull" || c.phase === "first pull saving")) timer = setTimeout(() => load(), 2000);
}

/* ---------------------------------------------------------------- setting sources up (admins) */
function guide(vendor) {
  const g = SETUP.guides[vendor], kids = [el("b", {text: g.label})];
  if (g.register) kids.push(el("p", {}, "Register at ", el("a", {href: g.register, target: "_blank", rel: "noopener noreferrer", text: g.register})));
  kids.push(el("ol", {}, ...g.steps.map(s => el("li", {text: s}))));
  if (g.sandbox) kids.push(el("p", {}, "Sandbox FHIR base: ", el("code", {text: g.sandbox}), " ",
    el("button", {class: "btn small", type: "button", text: "Use it", onclick: () => {
      $("#fIssuer").value = g.sandbox; if (!$("#fName").value) $("#fName").value = g.sandbox_name || "Epic sandbox";
      if (vendor === "insurer") { $("#fAuth").value = "confidential"; $("#fSecretWrap").hidden = false; } }})));
  if (g.scopes) kids.push(el("p", {}, "Scopes: ", el("code", {text: g.scopes})));
  if (g.portals) kids.push(el("p", {text: "Where individuals may be able to register (read November 2026; check each portal's terms):"}),
    el("ul", {class: "conn-portals"}, ...g.portals.map(p => el("li", {}, el("a", {href: p.url, target: "_blank", rel: "noopener noreferrer", text: p.name}), ": " + p.note))));
  for (const n of g.notes) kids.push(el("p", {class: "muted", text: n}));
  $("#guide").replaceChildren(...kids);
}

function fill(s) {
  editing = s ? s.id : null;
  $("#formTitle").textContent = s ? `Change ${s.name}` : "Add a source";
  $("#fVendor").value = s ? s.vendor : "epic";
  $("#fName").value = s ? s.name : "";
  $("#fIssuer").value = s ? s.issuer : "";
  $("#fClient").value = s ? s.client_id : "";
  $("#fAuth").value = s ? s.auth : "public";
  $("#fSecret").value = "";
  $("#fSecret").placeholder = s && s.has_secret ? "Kept; type a new one to replace it" : "";
  $("#fScopes").value = s ? s.scopes : SETUP.default_scopes;
  $("#fCancel").hidden = !s;
  $("#fSecretWrap").hidden = $("#fAuth").value !== "confidential";
  guide($("#fVendor").value);
}

function testResult(t) {
  if (!t.ok) return el("p", {class: "msg bad", text: "Test failed: " + t.problem});
  return el("div", {}, el("p", {class: "msg", text: "Discovery works. The server contacts only: " + t.hosts.join(", ")}),
    el("p", {class: "muted", text: "Suggested scopes: " + t.suggested_scopes}),
    ...t.notes.map(n => el("p", {class: "muted", text: n})));
}

function sourceRow(s) {
  const n = s.connections, out = el("div", {class: "prof"});
  const msg = el("div", {role: "status"});
  out.append(el("b", {text: s.name}), el("span", {class: "tag", text: SETUP.guides[s.vendor].label}),
    el("p", {class: "muted", text: `${s.issuer} · ${s.auth} client · ${s.tested ? "tested " + when(s.tested) : "not tested yet: people can't connect until it is"}`}),
    el("p", {class: "muted", text: `On this install: ${plural(n.active, "active connection")}, ${n["needs reconnect"]} needing a reconnect, ${n.disconnected} disconnected. This count is never sent anywhere.`}),
    el("div", {class: "row"},
      el("button", {class: "btn small primary", type: "button", text: "Test this connection", onclick: async () => {
        msg.replaceChildren(el("p", {class: "muted", text: "Reading its discovery…"}));
        try { const t = await call(`connect/sources/${s.id}/test`, "POST"); msg.replaceChildren(testResult(t)); if (t.ok) loadSetup(); }
        catch (e) { msg.replaceChildren(el("p", {class: "msg bad", text: e.message})); } }}),
      el("button", {class: "btn small", type: "button", text: "Change", onclick: () => { fill(s); $("#fName").focus(); }}),
      el("button", {class: "btn small", type: "button", text: "Remove", onclick: async () => {
        try { await call(`connect/sources/${s.id}`, "DELETE"); loadSetup(); } catch (e) { msg.replaceChildren(el("p", {class: "msg bad", text: e.message})); } }})),
    msg);
  return out;
}

async function loadSetup() {
  try { SETUP = await call("connect/sources"); }
  catch (e) {
    $("#admin").hidden = false;
    $("#admin").replaceChildren(el("h2", {text: "Set up a source"}), el("p", {class: "msg bad",
      text: e.message === "admin_two_factor_required" ? "Turn on two-factor sign-in for your account (Account page) to set up sources." : e.message}));
    return;
  }
  $("#admin").hidden = false;
  $("#returnAddr").textContent = SETUP.return_address || "(not set: see below)";
  $("#copyReturn").hidden = !SETUP.return_address;
  $("#addrWarn").replaceChildren(...SETUP.warnings.map(w => el("li", {text: w})));
  $("#sourceList").replaceChildren(...SETUP.sources.map(sourceRow));
  if (editing === null) fill(null);
}

$("#copyReturn").onclick = () => { try { navigator.clipboard.writeText(SETUP.return_address); } catch (e) {} };
$("#fVendor").onchange = () => {
  const v = $("#fVendor").value, sc = $("#fScopes");
  // an untouched default follows the vendor: an insurer's API is claims, coverage and the patient (#92)
  if ([SETUP.default_scopes, SETUP.insurer_scopes].includes(sc.value.trim())) sc.value = v === "insurer" ? SETUP.insurer_scopes : SETUP.default_scopes;
  guide(v);
};
$("#fAuth").onchange = () => { $("#fSecretWrap").hidden = $("#fAuth").value !== "confidential"; };
$("#fCancel").onclick = () => fill(null);
$("#srcForm").onsubmit = async ev => {
  ev.preventDefault();
  const body = {vendor: $("#fVendor").value, name: $("#fName").value, issuer: $("#fIssuer").value, client_id: $("#fClient").value,
    auth: $("#fAuth").value, scopes: $("#fScopes").value};
  if ($("#fSecret").value) body.secret = $("#fSecret").value;
  const m = $("#fMsg");
  try {
    const s = await call(editing ? `connect/sources/${editing}` : "connect/sources", editing ? "PATCH" : "POST", body);
    m.classList.remove("bad"); m.textContent = `Saved ${s.name}. Now test it.`;
    editing = null; loadSetup();
  } catch (e) { m.classList.add("bad"); m.textContent = e.message; }
};

/* ---------------------------------------------------------------- start */
(async function () {
  try { ME = await call("me"); } catch (e) { return; }
  await datesReady();   // dates in this person's time zone and format (dates.js)
  $("#who").textContent = ME.user.username;
  { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
  if (ME.profiles.length) {
    const sel = $("#profile");
    sel.replaceChildren(...ME.profiles.map(p => el("option", {value: p.id, text: p.name})));
    let want; try { want = +(params.get("profile") || localStorage.getItem("ht-profile")); } catch (e) {}
    if (ME.profiles.some(p => p.id === want)) sel.value = want;
    PID = +sel.value;
    sel.onchange = () => { PID = +sel.value; load(); };
    $("#mine").hidden = false;
    load(params.has("connected") ? "Connected. The first pull starts now; its preview shows here when it's ready." : null);
  }
  if (ME.user.is_admin) loadSetup();
})();
