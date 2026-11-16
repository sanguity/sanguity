/* Sanguity · connected sources (#32, #91): a profile's connections (sign in to a source, its first pull previewed like
   an upload, then synced on a schedule; Sync now, Reconnect when access ended, Disconnect keeping or removing the
   records), and for admins the guided setup of each source (option 1: this install's own app with each vendor).
   window.sanguityImport.follow (import-jobs.js) shows the first pull's preview. */
"use strict";   // $, el and api come from util.js
async function call(path, method = "GET", body) {
  const r = await api(path, {method, body: body ? JSON.stringify(body) : undefined});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
  return j;
}
const when = t => t ? new Date(t * 1000).toLocaleString(undefined, {year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) : "—";
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
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

function card(c, canEdit) {
  const box = el("div", {class: "prof conn-item" + (c.state === "needs reconnect" ? " conn-bad" : "")});
  box.append(el("b", {text: c.source}), el("span", {class: "tag", text: c.host}));
  box.append(el("p", {text: PHASE[c.phase] || c.phase}));
  if (c.error && c.state === "active") {
    box.append(el("p", {class: c.error_kind === "unavailable" ? "muted" : "msg bad",
      text: (c.error_kind === "unavailable" ? "Vendor unavailable: " : "Last pull failed: ") + c.error}));
  } else if (c.error && c.state === "needs reconnect") box.append(el("p", {class: "msg bad", text: c.error}));
  if (c.state !== "disconnected") {
    box.append(el("p", {class: "muted", text: `Last pull ${when(c.last_sync)}` + (c.next_sync && c.state === "active" ? ` · next ${when(c.next_sync)}` : "")
      + ` · ${plural(c.imports, "import")} in the history` + (c.credential === "access" ? " · no refresh token: sign in again when access runs out" : "")}));
    const rep = reportLine(c.report);
    if (rep) box.append(rep);
    for (const w of (c.report && c.report.warnings) || []) box.append(el("p", {class: "muted", text: w}));
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
  $("#viewerNote").hidden = d.can_connect;
  $("#noSources").hidden = !!d.sources.length || !d.can_connect;
  $("#sourceButtons").replaceChildren(...(d.can_connect ? d.sources : []).map(s =>
    el("a", {class: "btn", href: `api/profiles/${PID}/connect/start?source=${s.id}`, text: `Connect ${s.name}`, title: `Signs in at ${s.host}`})));
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
    el("button", {class: "btn small", type: "button", text: "Use it", onclick: () => { $("#fIssuer").value = g.sandbox; if (!$("#fName").value) $("#fName").value = "Epic sandbox"; }})));
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
$("#fVendor").onchange = () => guide($("#fVendor").value);
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
