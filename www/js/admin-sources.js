/* Sanguity · the Admin page's parts about getting records in (#167, after admin.js): Connected-source apps, the
   guided setup of each source (option 1: this install's own app with each vendor, docs/connected-sources-oauth.md)
   and the defaults of every connection; and the Provider directory, what Find your provider has and how to add
   Epic's and Oracle Health's lists. Uses call(), say(), when() and plural() from admin.js. */
"use strict";
/* ---------------------------------------------------------------- setting sources up (from connect.js, #32, #91) */
let SETUP = null, editing = null;
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
  prefill();
}
/* Sources' Find your provider (#166) sends an admin here to set a provider up: admin.html?vendor=…&issuer=…&name=…#apps
   fills the form in with it, once */
let prefilled = false;
function prefill() {
  const p = new URLSearchParams(location.search);
  if (prefilled || !p.get("issuer")) return;
  prefilled = true;
  if (SETUP.guides[p.get("vendor")]) { $("#fVendor").value = p.get("vendor"); $("#fVendor").onchange(); }
  $("#fIssuer").value = p.get("issuer");
  $("#fName").value = p.get("name") || "";
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

/* ---------------------------------------------------------------- the defaults of every connection (#101, #102) */
/* The install's own settings (server/settings.py, scope "install"), from GET settings/install: one Save for the
   section, and Reset to defaults. A connection can change its own on the Connected sources page. */
/* a whole number, on or off, some of a few choices (the install's sync.* settings, #101) */
function numberEditor(value, changed, ro) {
  const input = el("input", {type: "number", step: 1, min: 1, value, disabled: ro, oninput: changed, class: "set-num", "aria-label": "Value"});
  return {node: input, get: () => input.value === "" ? null : +input.value};
}
function toggleEditor(value, changed, ro) {
  const input = el("input", {type: "checkbox", checked: value ? "" : null, disabled: ro, onchange: changed});
  return {node: el("label", {class: "check"}, input, "On"), get: () => input.checked};
}
function choicesEditor(value, changed, ro, setting) {
  const boxes = Object.entries(setting.choices || {}).map(([id, label]) => el("label", {class: "check"},
    el("input", {type: "checkbox", value: id, checked: value.includes(id) ? "" : null, disabled: ro, onchange: changed}), label));
  return {node: el("div", {class: "row set-choices"}, ...boxes),
    get: () => boxes.map(b => b.querySelector("input")).filter(i => i.checked).map(i => i.value)};
}
const SYNC_EDITORS = {number: numberEditor, toggle: toggleEditor, choices: choicesEditor};
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

async function loadSync(note) {
  const box = $("#sec-sync");
  let d;
  try { d = await call("settings/install"); }
  catch (e) { box.replaceChildren(el("h2", {text: "Sync defaults"}), el("p", {class: "msg bad", text: e.message})); return; }
  const sec = d.sections.find(s => s.id === "sync");
  if (!sec) { box.hidden = true; return; }
  drawSync(sec, note);
}
function drawSync(sec, note) {
  const msg = el("span", {class: "msg", role: "status"});
  const save = el("button", {class: "btn primary small", type: "button", id: "syncSave", text: "Save", disabled: true});
  const eds = [];
  const changed = () => { save.disabled = eds.every(({st, ed}) => same(ed.get(), st.value)); msg.textContent = ""; msg.classList.remove("bad"); };
  const rows = sec.settings.map(st => {
    const ed = (SYNC_EDITORS[st.kind] || numberEditor)(st.value, changed, false, st);
    eds.push({st, ed});
    return el("div", {class: "set", "data-key": st.key},
      el("h3", {class: "sub"}, st.label, el("span", {class: "tag", text: st.from === "install" ? "set for this server" : "default"})),
      st.help ? el("p", {class: "hint", text: st.help}) : null,
      ed.node);
  });
  save.onclick = async () => {   // each setting that changed, in turn; the first refusal stops and says why
    save.disabled = true;
    const todo = eds.filter(({st, ed}) => !same(ed.get(), st.value));
    let last;
    for (const {st, ed} of todo) {
      try { last = await call(`settings/install/${encodeURIComponent(st.key)}`, "PUT", {value: ed.get()}); }
      catch (e) {
        msg.textContent = `“${st.label}”: ${e.message}`; msg.classList.add("bad"); save.disabled = false;
        if (last) for (const x of last.sections.find(s => s.id === "sync").settings) { const o = eds.find(y => y.st.key === x.key); if (o) o.st.value = x.value; }
        return;
      }
    }
    drawSync(last.sections.find(s => s.id === "sync"), `Saved ${todo.length === 1 ? `“${todo[0].st.label}”` : "the sync defaults"}.`);
  };
  const reset = el("button", {class: "btn small", type: "button", text: "Reset to defaults", disabled: !sec.settings.some(st => st.from === "install"),
    onclick: async () => {
      if (!confirm("Put the sync defaults back to Sanguity's own?")) return;
      reset.disabled = true;
      try { drawSync((await call("settings/install/reset", "POST", {section: "sync"})).sections.find(s => s.id === "sync"), "The sync defaults are back to Sanguity's own."); }
      catch (e) { msg.textContent = e.message; msg.classList.add("bad"); reset.disabled = false; }
    }});
  if (note) msg.textContent = note;
  $("#sec-sync").replaceChildren(
    el("div", {class: "set-hd"}, el("h2", {text: sec.title}), reset),
    sec.help ? el("p", {class: "hint", text: sec.help}) : null,
    ...rows,
    el("div", {class: "row"}, save, msg));
}

/* ---------------------------------------------------------------- the provider directory (#93) */
/* What Find your provider has on this server (GET directory, any signed-in account: the lists' names, never a
   search), and how an admin adds Epic's and Oracle Health's published lists (docs/provider-directory.md). */
async function loadDirectory() {
  let j;
  try { j = await call("directory?q="); }
  catch (e) { say("#dirMsg", e.message, true); return; }
  const missing = j.sources.filter(s => !s.included);
  const why = s => (s.why || "").replace(/ Run tools.*$/, "");
  $("#dirSources").replaceChildren(
    el("p", {}, el("b", {text: `${j.total.toLocaleString()} providers, insurers and sandboxes`}), ` in this server's list, from:`),
    el("ul", {class: "adm-dir"}, ...j.sources.map(s => el("li", {},
      el("b", {text: s.label}), " · ",
      s.included ? `loaded${s.entries ? `, ${s.entries.toLocaleString()} entries` : ""}${s.retrieved ? `, read ${s.retrieved}` : ""}`
        : el("span", {class: "bad", text: "not loaded"}),
      s.included ? ". " : `. ${why(s)} `,
      el("a", {href: s.terms, target: "_blank", rel: "noopener noreferrer", text: "Terms"})))),
    el("p", {}, el("a", {href: "sources.html#portals", text: "Open Find your provider"})));
  $("#dirAdd").replaceChildren(
    el("h3", {class: "sub", text: missing.length ? `Adding ${missing.map(s => s.label.split(":")[0].replace(/ Millennium.*/, "")).join(" and ")}'s lists` : "Keeping the lists up to date"}),
    el("p", {}, "From the Sanguity folder on the server (Python 3.9 or later, nothing to install), run:"),
    el("div", {class: "dir-src"}, el("pre", {}, el("code", {text: "python3 tools/build_directory.py"}))),
    el("p", {class: "hint", text: "It downloads Epic's and Oracle Health's lists (about 100 MB from Epic, 4 MB from Oracle), and the next " +
      "search uses them, with no restart."}),
    moreAbout("It writes this server's own copy, data/directory/providers.json.gz. Run it again now and then (Epic suggests weekly) to " +
      "pick up new hospitals. It's the only part of Sanguity that downloads a list, and only when you run it. More in docs/provider-directory.md."));
  $("#directory").hidden = false;
}
