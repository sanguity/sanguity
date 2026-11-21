/* Sanguity · the Admin page's parts about getting records in (#167, after admin.js): Connected-source apps, the
   guided setup of each source (option 1: this install's own app with each vendor, docs/connected-sources-oauth.md)
   and the defaults of every connection; and the Provider directory, what Find your provider has and how to add
   Epic's and Oracle Health's lists. Uses call(), say(), when() and plural() from admin.js. */
"use strict";
/* ---------------------------------------------------------------- setting sources up (from connect.js, #32, #91) */
/* The walk-through (#165): for the vendor chosen, numbered steps of what to do on its developer site (from the
   server's guide, connections.GUIDES: what to look for, since vendors' sites change their wording), each with the
   values to paste and a Copy button; then the form's fields as a step of their own, with "Waiting for the vendor's
   review"; then Test this connection, for the source just saved or being changed. */
let SETUP = null, editing = null, saved = null;
const scopeTypes = scopes => [...new Set((scopes.match(/(?:patient|user)\/([A-Za-z]+)\./g) || []).map(s => s.split("/")[1].slice(0, -1)))];
const PASTE = {
  return_address: {label: "Return address", get: () => SETUP.return_address},
  scopes: {label: "Scopes", get: () => $("#fScopes").value.trim()},
  types: {label: "Types it reads", get: () => scopeTypes($("#fScopes").value).join(", ")},
  sandbox: {label: "Sandbox FHIR base", get: g => g.sandbox},
};
/* a value to paste, and the app's one Copy (util.js copyBtn, #495: it selects the value instead where the browser keeps
   the clipboard from the page, plain HTTP); its text as it is when pressed (the scopes follow what's typed) */
function copyLine(key, g) {
  const p = PASTE[key], value = p.get(g);
  if (!value) return key === "return_address"
    ? el("p", {class: "msg bad", text: "No return address yet: see the warning at the top of this section."}) : null;
  const code = el("code", {"data-paste": key, text: value});
  const done = el("span", {class: "msg"});
  const use = key === "sandbox" ? el("button", {class: "btn small", type: "button", text: "Use it", onclick: () => {
    $("#fIssuer").value = g.sandbox; if (!$("#fName").value) $("#fName").value = g.sandbox_name || "Sandbox";
    if ($("#fVendor").value === "insurer") { $("#fAuth").value = "confidential"; $("#fSecretWrap").hidden = false; }
    sayIn(done, "Filled in below.");
  }}) : null;
  return el("div", {class: "copy-val"}, el("span", {class: "copy-val-name", text: p.label}),
    el("div", {class: "copy-val-row"}, code, copyBtn(() => code.textContent, {what: "the " + p.label.toLowerCase(), sel: code}), use, use ? done : null));
}
/* a step: a step card, as Phone setup's are (app.css .steps, #494), its heading an h4 under the form's h3 */
function stepItem(title, ...body) {
  return el("li", {class: "step"}, el("h4", {class: "step-hd", text: title}), ...body);
}
function guide(vendor) {
  const g = SETUP.guides[vendor];
  const steps = g.steps.map(s => stepItem(s.title, el("p", {text: s.text}),
    s.link && g.register ? el("p", {}, el("a", {href: g.register, target: "_blank", rel: "noopener noreferrer", text: g.register})) : null,
    s.portals && g.portals ? el("ul", {class: "conn-portals"}, ...g.portals.map(p => el("li", {},
      el("a", {href: p.url, target: "_blank", rel: "noopener noreferrer", text: p.name}), ": " + p.note))) : null,
    ...(s.paste || []).map(k => copyLine(k, g))));
  steps.push(stepItem(`Fill in what ${g.reviewer} gave you`,
    el("p", {text: "Then Save. Tick Waiting for the vendor's review if it's still reviewing the app."}), $("#srcFields")));
  steps.push(testStep());
  $("#guide").replaceChildren(...[
    el("b", {text: g.label}),
    g.about ? el("p", {text: g.about}) : null,
    el("p", {class: "hint", text: "Look for what each step describes: vendors change their sites' wording."}),
    el("ol", {class: "steps walk", id: "walk", role: "list", "aria-label": `Setting up ${g.label}`}, ...steps),
    // its notes behind a disclosure named for them (#494): what's unverified, what older apps need
    g.notes.length ? el("details", {class: "more walk-notes"}, el("summary", {text: `Notes on ${g.label}`}), ...g.notes.map(n => el("p", {text: n}))) : null].filter(Boolean));
}
/* the last step: test the source being changed, or the one just saved */
function testStep() {
  const s = SETUP.sources.find(x => x.id === (editing ?? saved));
  const out = el("div", {id: "walkResult", role: "status"});
  const btn = el("button", {class: "btn primary", type: "button", id: "walkTest", text: s ? `Test ${s.name}` : "Test this connection",
    disabled: !s, onclick: async () => {
      out.replaceChildren(el("p", {class: "muted", text: "Reading its discovery…"}));
      try {
        const t = await call(`connect/sources/${s.id}/test`, "POST"), now = SETUP.sources.find(x => x.id === s.id) || s;   // (#506: its waiting since changed on its row)
        out.replaceChildren(...present(testResult(t), t.ok && now.waiting ? el("p", {class: "msg", text: "It's still marked as waiting for the " +
          "vendor's review, so nobody can connect it yet. Once the review is done, untick that above and save."}) : null,
          t.ok && !now.waiting ? el("p", {class: "msg", text: "Done: people can now connect it under Sources → Portals and health plans."}) : null));
        if (t.ok) loadSetup({walk: false});   // the list's "tested" now; this step, its result and focus kept (#502)
      } catch (e) { out.replaceChildren(el("p", {class: "msg bad", role: "alert", text: e.message})); }
    }});
  return stepItem("Test this connection",
    el("p", {text: "This reads what the vendor publishes about its server (its discovery) to check the address and what it " +
      "supports. Nobody signs in. Until it works, nobody can connect the source."}),
    s ? null : el("p", {class: "hint", text: "Save the source first."}),
    el("div", {class: "row"}, btn), out);
}

/* the form on a source (s), or empty to add one: what it said about another, or about this one before, cleared (#510:
   "Saved …" stayed beside an empty form once its source was removed, and beside another opened in Change) */
function fill(s) {
  editing = s ? s.id : null;
  sayIn($("#fMsg"), "");
  $("#formTitle").textContent = s ? `Change ${s.name}` : "Add a source";
  $("#fVendor").value = s ? s.vendor : "epic";
  $("#fName").value = s ? s.name : "";
  $("#fIssuer").value = s ? s.issuer : "";
  $("#fClient").value = s ? s.client_id : "";
  $("#fAuth").value = s ? s.auth : "public";
  $("#fSecret").value = "";
  $("#fSecret").placeholder = s && s.has_secret ? "Kept; type a new one to replace it" : "";
  $("#fScopes").value = s ? s.scopes : SETUP.default_scopes;
  $("#fWaiting").checked = !!(s && s.waiting);
  $("#fCancel").hidden = !s;
  $("#fCancel").textContent = s && s.id === saved ? "Add another source" : "Cancel";
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
  const n = s.connections, out = el("div", {class: "prof", "data-src": s.id});
  const msg = el("div", {class: "src-msg", role: "status"});
  const state = s.waiting ? `waiting for the vendor's review since ${when(s.waiting)}: people are told to upload their portal's export meanwhile`
    : s.tested ? "tested " + when(s.tested) : "not tested yet: people can't connect until it is";
  out.append(...present(el("b", {text: s.name}), el("span", {class: "tag", text: SETUP.guides[s.vendor].label}),
    s.waiting ? el("span", {class: "tag walk-tag", text: "Waiting for review"}) : null,
    el("p", {class: "muted", text: `${s.issuer} · ${s.auth} client · ${state}`}),
    el("p", {class: "muted", text: `On this install: ${plural(n.active, "active connection")}, ${n["needs reconnect"]} needing a reconnect, ${n.disconnected} disconnected. This count is never sent anywhere.`}),
    (s.leaves_out || []).length ? el("p", {class: "muted src-leaves", text: `Its scopes leave out ${s.leaves_out.join(", ").toLowerCase()}, ` +   // #228
      "so people can't choose them. To offer them, add their scopes under Change and in the app's registration at the vendor."}) : null,
    el("div", {class: "row"},
      el("button", {class: "btn small primary", type: "button", text: "Test this connection", "data-act": "test", onclick: async () => {
        msg.replaceChildren(el("p", {class: "muted", text: "Reading its discovery…"}));
        try { const t = await call(`connect/sources/${s.id}/test`, "POST"); msg.replaceChildren(testResult(t)); if (t.ok) rowsAgain(s.id, "test", [...msg.childNodes]); }
        catch (e) { msg.replaceChildren(el("p", {class: "msg bad", role: "alert", text: e.message})); } }}),
      el("button", {class: "btn small", type: "button", text: "Change", onclick: () => { saved = null; fill(s); $("#fName").focus(); }}),
      el("button", {class: "btn small", type: "button", text: s.waiting ? "Review done" : "Waiting for review", "data-act": "wait",
        "data-tip": s.waiting ? "The vendor has approved the app: people can connect it once it's tested" : "Say the vendor is still reviewing the app",
        onclick: async () => {
          try { await call(`connect/sources/${s.id}`, "PATCH", {waiting: !s.waiting}); rowsAgain(s.id, "wait"); }
          catch (e) { msg.replaceChildren(el("p", {class: "msg bad", role: "alert", text: e.message})); } }}),
      el("button", {class: "btn small", type: "button", text: "Remove", "data-act": "remove", onclick: async () => {   // asked first (#502)
        if (!await ask({title: `Remove ${s.name}?`, message: "Nobody can connect it any more. Its app at the vendor stays as it is.",
                        action: "Remove source", danger: true})) return;
        try { await call(`connect/sources/${s.id}`, "DELETE"); rowsAgain(s.id); } catch (e) { msg.replaceChildren(el("p", {class: "msg bad", role: "alert", text: e.message})); } }})),
    msg));
  return out;
}
/* the list drawn again after a row's action (#502: focus fell to the page, and a test's result went with its row):
   focus on the same button (act) of the source's new row, what it said (kept: a test's result) back under it, in view;
   the source gone, on the next row's Test, else the one before's, else on the card's heading. Only the list (#506:
   the walk-through drawn again emptied a new source half typed in); the source open in Change takes its new waiting,
   which Save would otherwise undo, and the form goes back to Add a source once it's removed */
async function rowsAgain(id, act, kept) {
  const rows = () => [...$("#sourceList").querySelectorAll(".prof[data-src]")], at = rows().findIndex(r => r.dataset.src === String(id));
  await loadSetup({walk: false});
  if (editing === id) {
    const s = SETUP.sources.find(x => x.id === id);
    if (!s) { saved = null; fill(null); }
    else $("#fWaiting").checked = !!s.waiting;
  }
  const r = act && rows().find(r => r.dataset.src === String(id));
  if (!r) return focusNear(rows(), Math.max(at, 0), "[data-act=test]", $("#admin h2"));
  const msg = r.querySelector(".src-msg");
  if (kept) { msg.replaceChildren(...kept); msg.scrollIntoView({block: "nearest"}); }
  focusNear([r], 0, `[data-act=${act}]`);
}

/* walk: false keeps the walk-through's last step as it is (its own test, which said how it went) */
async function loadSetup({walk = true} = {}) {
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
  $("#sourceList").replaceChildren(...SETUP.sources.length ? SETUP.sources.map(sourceRow)   // none: said so (util.js, #499: it showed nothing)
    : [emptyState("No source set up yet: each one added below is listed here, for people to connect.", null, {cls: ""})]);
  if (walk && editing === null) fill(null);
  else if (walk) {   // the source being changed, as saved now; the form keeps what's typed in it
    const s = SETUP.sources.find(x => x.id === editing);
    if (s) $("#walk").lastElementChild.replaceWith(testStep());
    else fill(null);
  }
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

{   // the return address's Copy at the top, the app's one (#495: it copied silently, and a refusal went unhandled)
  const b = copyBtn(() => SETUP.return_address, {what: "the return address", sel: $("#returnAddr")});
  b.id = "copyReturn"; $("#copyReturn").replaceWith(b);
}
$("#fVendor").onchange = () => {
  const v = $("#fVendor").value, sc = $("#fScopes");
  // an untouched default follows the vendor: an insurer's API is claims, coverage and the patient (#92)
  if ([SETUP.default_scopes, SETUP.insurer_scopes].includes(sc.value.trim())) sc.value = v === "insurer" ? SETUP.insurer_scopes : SETUP.default_scopes;
  guide(v);
};
/* the scopes typed in are the ones the walk-through says to ask for */
$("#fScopes").addEventListener("input", () => {
  for (const c of document.querySelectorAll("#guide [data-paste]")) if (c.dataset.paste !== "return_address" && c.dataset.paste !== "sandbox") c.textContent = PASTE[c.dataset.paste].get();
});
$("#fAuth").onchange = () => { $("#fSecretWrap").hidden = $("#fAuth").value !== "confidential"; };
$("#fCancel").onclick = () => { saved = null; fill(null); };
pwField($("#fSecret"), {name: "client secret"});   // Show and Hide, as every password field has (util.js, #492)
$("#srcForm").onsubmit = async ev => {
  ev.preventDefault();
  pwSent(ev.target);
  const body = {vendor: $("#fVendor").value, name: $("#fName").value, issuer: $("#fIssuer").value, client_id: $("#fClient").value,
    auth: $("#fAuth").value, scopes: $("#fScopes").value, waiting: $("#fWaiting").checked};
  if ($("#fSecret").value) body.secret = $("#fSecret").value;
  const m = $("#fMsg");
  try {
    const s = await call(editing ? `connect/sources/${editing}` : "connect/sources", editing ? "PATCH" : "POST", body);
    saved = s.id;
    await loadSetup();
    fill(SETUP.sources.find(x => x.id === s.id));   // the walk-through stays on it, at its last step: the test
    sayIn($("#fMsg"), s.waiting ? `Saved ${s.name}, waiting for the vendor's review. You can test it now or once the review is done.`
      : `Saved ${s.name}. Last step: test it.`);
    $("#walkTest").focus();
  } catch (e) { sayIn(m, e.message, true); }
};

/* ---------------------------------------------------------------- the defaults of every connection (#101, #102) */
/* The install's own settings (server/settings.py, scope "install"), from GET settings/install, saved as Settings'
   are (#272): on or off and the kinds ticked as they're changed, a toast saying so with an Undo; the numbers kept as
   typed, marked "not saved" and counted in the card's data-dirty, until the group's bar (sidebar.js, admin.js) saves
   them (syncSave) or puts them back (syncDiscard). One saved as it's changed isn't counted while it's on its way,
   only once the server refuses it (held, #407). "set for this server" and Reset to defaults show only when one
   isn't Sanguity's own default, and a Reset has an Undo too. A connection can change its own on the Connected
   sources page. */
/* a whole number, on or off, some of a few choices (the install's sync.* settings, #101); each control has an id, for
   focus to come back to once the card is drawn again (a toast's Undo) */
function numberEditor(value, changed, ro, setting) {
  const input = el("input", {type: "number", step: 1, min: 1, value, disabled: ro, oninput: changed, class: "set-num", "aria-label": "Value", id: setting.key});
  return {node: input, get: () => input.value === "" ? null : +input.value};
}
function toggleEditor(value, changed, ro, setting) {   // a switch (#259), named by the setting's heading above it
  const input = el("input", {type: "checkbox", role: "switch", id: setting.key, "aria-label": setting.label, checked: value ? "" : null, disabled: ro, onchange: changed});
  return {node: el("label", {class: "check switch"}, input), get: () => input.checked};
}
function choicesEditor(value, changed, ro, setting) {
  const boxes = Object.entries(setting.choices || {}).map(([id, label]) => el("label", {class: "check"},
    el("input", {type: "checkbox", id: `${setting.key}-${id}`, value: id, checked: value.includes(id) ? "" : null, disabled: ro, onchange: changed}), label));
  return {node: el("div", {class: "row set-choices"}, ...boxes),
    get: () => boxes.map(b => b.querySelector("input")).filter(i => i.checked).map(i => i.value)};
}
const SYNC_EDITORS = {number: numberEditor, toggle: toggleEditor, choices: choicesEditor};
const SYNC_NOW = new Set(["toggle", "choices"]);   // saved as they're changed
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const syncList = labels => labels.map((l, i) => (i === 0 ? "" : i === labels.length - 1 ? " and " : ", ") + `“${l}”`).join("");
let SYNC = [];   // the settings drawn: {st, dirty(), counts(), held, get(), put(value)}

async function loadSync(note) {
  const box = $("#sec-sync");
  let d;
  try { d = await call("settings/install"); }
  catch (e) { box.replaceChildren(el("h2", {text: "Sync defaults"}), el("p", {class: "msg bad", text: e.message})); return; }
  const sec = d.sections.find(s => s.id === "sync");
  if (!sec) { box.hidden = true; return; }
  drawSync(sec, note);
}
/* drawn again from the server's answer; keep: the settings changed and not saved, kept as typed */
function drawSync(sec, note, keep = []) {
  const card = $("#sec-sync"), msg = el("span", {class: "msg set-msg"}), back = card.contains(document.activeElement) ? document.activeElement.id : null;
  const count = () => { const n = SYNC.filter(e => e.counts()).length; if (n) card.setAttribute("data-dirty", n); else card.removeAttribute("data-dirty"); };
  const reset = el("button", {class: "btn small", type: "button", text: "Reset to defaults", hidden: sec.settings.every(st => same(st.value, st.default)) || null, onclick: () => syncReset(sec)});
  SYNC = sec.settings.map(st => {
    const node = el("div", {class: "set set-row", "data-key": st.key}), was = keep.find(k => k.st.key === st.key), e = {st, held: !!was?.held, queue: was?.queue};
    const mark = () => { node.classList.toggle("set-changed", e.counts()); count(); };
    const make = v => (SYNC_EDITORS[st.kind] || numberEditor)(v, () => {
      e.held = false; mark();
      if (SYNC_NOW.has(st.kind) && e.dirty()) syncNow(e);
    }, false, st);
    let ed = make(was ? was.get() : st.value);
    Object.assign(e, {node, mark, dirty: () => !same(ed.get(), st.value), counts: () => e.dirty() && (!SYNC_NOW.has(st.kind) || e.held),
      get: () => ed.get(), put: v => { const old = ed.node; ed = make(v); old.replaceWith(ed.node); }});
    node.classList.toggle("set-changed", e.counts());
    // a setting row (#286): its name and help on the left, its control on the right, as Settings' are
    node.append(el("div", {class: "set-row-lbl"}, el("h3", {class: "sub"}, st.label, same(st.value, st.default) ? null : el("span", {class: "tag", text: "set for this server"}),
      el("span", {class: "tag set-unsaved", text: "not saved"})), helpNodes(st.help)), el("div", {class: "set-row-ctl"}, ed.node));
    return e;
  });
  if (note) sayIn(msg, note, false, {say: false});
  card.replaceChildren(...present(
    el("div", {class: "set-hd"}, el("h2", {id: "sec-sync-hd", tabindex: -1, text: sec.title}), reset),
    helpNodes(sec.help), msg,
    ...SYNC.map(e => e.node)));
  count();
  if (back && !card.contains(document.activeElement)) document.getElementById(back)?.focus({preventScroll: true});   // (a control saved as it's changed)
}
const syncSec = d => d.sections.find(s => s.id === "sync");
/* an Undo: each setting put back as it was (was: [{key, label, value, wrote}]), only one still as written */
async function syncUndo(was) {
  const now = syncSec(await call("settings/install")).settings, back = [], since = [];
  for (const w of was) {
    if (!same(now.find(st => st.key === w.key)?.value, w.wrote)) { since.push(w.label); continue; }
    await call(`settings/install/${encodeURIComponent(w.key)}`, "PUT", {value: w.value});
    back.push(w.label);
  }
  const text = [back.length ? `Undone: ${syncList(back)} ${back.length === 1 ? "is" : "are"} as before.` : "",
    since.length ? `${syncList(since)} ${since.length === 1 ? "was" : "were"} changed since, so ${since.length === 1 ? "it's" : "they're"} left as ${since.length === 1 ? "it is" : "they are"}.` : ""].join(" ").trim();
  drawSync(syncSec(await call("settings/install")), text, SYNC.filter(e => e.dirty()));
  sayIn($("#sec-sync .set-msg"), text);
}
/* on or off, or the kinds ticked: saved at once, one at a time */
function syncNow(e) {
  e.queue = (e.queue || Promise.resolve()).then(async () => {
    if (!e.dirty()) return;
    const value = e.get(), was = [{key: e.st.key, label: e.st.label, value: e.st.value, wrote: value}];
    let d;
    try { d = await call(`settings/install/${encodeURIComponent(e.st.key)}`, "PUT", {value}); }
    catch (err) {
      const now = SYNC.find(x => x.st.key === e.st.key && x.dirty()) || e;   // (drawn again meanwhile: that one)
      sayIn($("#sec-sync .set-msg"), `“${e.st.label}” isn't saved: ${err.message}`, true); now.held = true; now.mark(); return;
    }
    const said = `Saved “${e.st.label}”.`;
    drawSync(syncSec(d), said, SYNC.filter(x => x !== e && x.dirty()));
    toast(said, {action: "Undo", onAction: () => syncUndo(was)});
  }).catch(() => {});
}
/* the bar's Save: each number changed, in turn; the first refusal stops it and says why */
async function syncSave(msg, had) {
  const todo = SYNC.filter(e => e.counts()), done = [], was = [];
  let last = null;
  for (const e of todo) {
    const value = e.get();
    try { last = await call(`settings/install/${encodeURIComponent(e.st.key)}`, "PUT", {value}); }
    catch (err) {
      sayIn(msg, (done.length ? `Saved ${syncList(done)}. ` : "") + `“${e.st.label}” isn't saved: ${err.message}`, true);
      if (last) drawSync(syncSec(last), "", SYNC.filter(x => x.dirty() && !done.includes(x.st.label)));
      return;
    }
    done.push(e.st.label); was.push({key: e.st.key, label: e.st.label, value: e.st.value, wrote: value});
  }
  const said = `Saved ${syncList(done)}.`;
  drawSync(syncSec(last), said);
  if (had) $("#sec-sync-hd").focus({preventScroll: true});
  toast(said, {action: "Undo", onAction: () => syncUndo(was)});
}
function syncDiscard() {
  for (const e of SYNC) if (e.counts()) { e.put(e.st.value); e.node.classList.remove("set-changed"); }   // (one on its way stays, #407)
  $("#sec-sync").removeAttribute("data-dirty");
  sayIn($("#sec-sync .set-msg"), "Changes discarded: back as saved.");
  $("#sec-sync-hd").focus();
}
/* Reset to defaults: at once, with an Undo; asked first only with changes not saved, which it loses */
async function syncReset(sec) {
  if ($("#sec-sync").hasAttribute("data-dirty") && !await ask({title: "Reset the sync defaults?", action: "Reset to defaults", danger: true,
      message: "They go back to Sanguity's own, for every connection that doesn't set its own, and your changes not saved yet are lost."})) return;
  const was = sec.settings.filter(st => !same(st.value, st.default)).map(st => ({key: st.key, label: st.label, value: st.value, wrote: st.default}));
  let d;
  try { d = await call("settings/install/reset", "POST", {section: "sync"}); }
  catch (e) { sayIn($("#sec-sync .set-msg"), e.message, true); return; }
  const said = "The sync defaults are back to Sanguity's own.";
  drawSync(syncSec(d), said);
  $("#sec-sync-hd").focus({preventScroll: true});
  toast(said, {action: "Undo", onAction: () => syncUndo(was)});
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
      s.included ? `loaded${s.entries ? `, ${s.entries.toLocaleString()} entries` : ""}${s.retrieved ? `, read ${fmtD(s.retrieved)}` : ""}`
        : el("span", {class: "bad", text: "not loaded"}),
      s.included ? ". " : `. ${why(s)} `,
      el("a", {href: s.terms, target: "_blank", rel: "noopener noreferrer", text: "Terms"})))),
    el("p", {}, el("a", {href: "sources.html#portals", text: "Open Find your provider"})));
  $("#dirAdd").replaceChildren(
    el("h3", {class: "sub", text: missing.length ? `Adding ${missing.map(s => s.label.split(":")[0].replace(/ Millennium.*/, "")).join(" and ")}'s lists` : "Keeping the lists up to date"}),
    el("p", {}, "From the Sanguity folder on the server (Python 3.9 or later, nothing to install), run this, and again now and then (Epic suggests weekly) to pick up new hospitals:"),   // (in sight, #447)
    el("div", {class: "dir-src"}, el("pre", {}, el("code", {text: "python3 tools/build_directory.py"}))),
    el("p", {class: "hint", text: "It downloads Epic's and Oracle Health's lists (about 100 MB from Epic, 4 MB from Oracle), and the next " +
      "search uses them, with no restart."}),
    aboutWords("It writes this server's own copy, data/directory/providers.json.gz. It's the only part of Sanguity that downloads a list, and only when you run it. More in docs/provider-directory.md."));
  $("#directory").hidden = false;
}
