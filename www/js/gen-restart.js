/* Sanguity · Settings → Install → General → Needs a restart (#1121): the settings the server reads only as it starts
   (server/server_settings.py: the upload, connected-source and Health Link limits, the password checks at once, the
   trusted proxies, the receiver's time zone and unclaimed data's days, what a restore replaced). Each is kept as typed,
   marked "not saved" and counted in the card's data-dirty until the group's bar saves them all at once (genRsSave,
   the fresh check in the card) or puts them back (genRsDiscard); saved, the card says Restart needed with Restart now,
   which stops the app for Docker to start again, then waits for it to answer and reads the card again. A new value is
   tried and kept once the server has run healthy with it; one that didn't is said here, with why. One the server's
   environment sets is plain text with its variable (no control). call(), sayIn(), ask() and plural() are the page's. */
"use strict";
let RS = null;   // the server's answer: {settings: [...], restart_needed, trying, fell_back, unreadable, settings_at}
const rsId = name => "rs-" + name.toLowerCase().replace(/_/g, "-");
const rsShown = (st, v) => st.kind === "number" ? `${Number(v).toLocaleString("en-US")} ${st.unit}` : String(v);
const rsTyped = () => RS ? RS.settings.filter(st => { const i = document.getElementById(rsId(st.name)); return i && i.value.trim() !== String(st.next ?? st.value); }) : [];

async function loadGenRestart(note) {
  try { RS = await call("settings/install/restart"); }
  catch (e) { sayIn($("#genRsMsg"), e.message, true); return; }
  rsDraw(note);
}
function rsDraw(note, typed = {}) {
  const body = $("#genRsBody"), back = body.contains(document.activeElement) ? document.activeElement.id : null, rows = [];
  let group = null;
  for (const st of RS.settings) {
    if (st.group !== group) rows.push(el("h3", {class: "sub", text: group = st.group}));
    const id = rsId(st.name), waits = "next" in st, hint = [st.help,
      st.kind === "number" ? `From ${st.min.toLocaleString("en-US")} to ${st.max.toLocaleString("en-US")}; ${rsShown(st, st.default)} unless changed.` : `${st.default} unless changed.`,
      waits ? `In force now: ${rsShown(st, st.value)}; ${rsShown(st, st.next)} after a restart.` : ""].filter(Boolean).join(" ");
    if (!st.can_change) {   // the environment's: its value as text, its variable, where to change it
      rows.push(setRow([el("p", {class: "set-row-name", id: id + "-lbl"}, st.label, el("span", {class: "tag", text: `Set in .env: ${st.name}`})),
        el("p", {class: "hint", text: hint}),
        el("p", {class: "hint", text: `Change it in ${RS.settings_at}, or take it out there to change it here.`})],
        el("p", {"aria-labelledby": id + "-lbl", text: rsShown(st, st.value)}), {"data-name": st.name}));
      continue;
    }
    const row = setRow([el("label", {for: id}, st.label, waits ? el("span", {class: "tag", text: "Restart needed"}) : null,
      el("span", {class: "tag set-unsaved", text: "not saved"})), el("p", {class: "hint", id: id + "-h", text: hint})], null, {"data-name": st.name});
    const input = el("input", st.kind === "number"
      ? {type: "number", id, step: 1, min: st.min, max: st.max, class: "set-num", "aria-describedby": id + "-h"}
      : {type: "text", id, spellcheck: "false", autocapitalize: "none", autocomplete: "off", "aria-describedby": id + "-h"});
    input.value = typed[st.name] ?? String(st.next ?? st.value);
    input.oninput = () => { row.classList.toggle("set-changed", input.value.trim() !== String(st.next ?? st.value)); rsCount(); };
    row.querySelector(".set-row-ctl").append(input);
    row.classList.toggle("set-changed", input.value.trim() !== String(st.next ?? st.value));
    rows.push(row);
  }
  body.replaceChildren(...rows);
  body.onkeydown = ev => { if (ev.key === "Enter" && ev.target.matches("input")) { ev.preventDefault(); $("#setBar .btn.primary")?.click(); } };   // Enter: the bar's Save
  rsState();
  rsCount();
  if (note) sayIn($("#genRsMsg"), note, false, {say: false});
  if (back && !body.contains(document.activeElement)) document.getElementById(back)?.focus({preventScroll: true});
}
/* what waits for, or came of, a restart: Restart needed with Restart now; the new values being checked; a fall back */
function rsState() {
  const box = $("#genRsState"), out = [];
  if (RS.unreadable) out.push(el("p", {class: "msg bad", text: RS.unreadable}));
  if (RS.fell_back) out.push(el("p", {class: "msg bad"}, el("b", {text: "The last start went back to the settings before. "}), RS.fell_back.why));
  if (RS.trying) out.push(el("p", {class: "hint", text: `The server started with new settings and is checking them: they're kept once it has run healthy for ${RS.promote_seconds} seconds.`}));
  if (RS.restart_needed) out.push(setRow([el("p", {class: "set-row-name", id: "rsNeedLbl"}, "Restart needed"),
      el("p", {class: "hint", id: "rsNeedHint", text: "Saved changes apply when the server starts again. Restart now stops it, and Docker starts it again in a few seconds; pages open meanwhile wait for it."})],
    el("button", {class: "btn", type: "button", id: "rsRestart", text: "Restart now", "aria-describedby": "rsNeedHint", onclick: rsRestart})));
  box.replaceChildren(...out);
}
function rsCount() {
  const n = rsTyped().length, card = $("#gen-restart");
  if (n) card.setAttribute("data-dirty", n); else card.removeAttribute("data-dirty");
}
const rsTypedNow = () => Object.fromEntries(rsTyped().map(st => [st.name, document.getElementById(rsId(st.name)).value]));
/* the group's bar: Save, every value typed in one change; refused, it says why and they stay as typed */
async function genRsSave(msg, had) {
  const todo = rsTyped();
  if (!todo.length) return;
  const values = Object.fromEntries(todo.map(st => { const raw = document.getElementById(rsId(st.name)).value.trim();
    return [st.name, raw === "" ? null : st.kind === "number" ? +raw : raw]; }));
  try { RS = await fresh(() => call("settings/install/restart/values", "PUT", {values}), $("#genRsReauth")); }
  catch (e) { sayIn(msg, `Not saved: ${e.why || e.message}`, true); return; }
  const said = `Saved ${todo.map(st => `“${st.label}”`).join(", ")}: ${RS.restart_needed ? "they apply once the server restarts." : "back as the server runs now."}`;
  rsDraw(said);
  if (had) $("#genRsHd").focus({preventScroll: true});
  toast(said);
}
function genRsDiscard() {
  rsDraw("Changes discarded: back as saved.");
  $("#genRsHd").focus();
}
async function rsRestart() {
  if (!await ask({title: "Restart the server now?", action: "Restart now",
    message: "Sanguity stops and starts again with the saved settings, which takes a few seconds; anyone using it meanwhile waits. If the new settings don't come up healthy, it goes back to the ones before and says so here."})) return;
  const msg = $("#genRsMsg");
  try { await fresh(() => call("settings/install/restart", "POST"), $("#genRsReauth")); }
  catch (e) { sayIn(msg, e.message, true); return; }
  $("#rsRestart").disabled = true;
  sayIn(msg, "Restarting…");
  await new Promise(r => setTimeout(r, 3000));
  for (let i = 0; i < 60; i++) {   // up again (Docker's restart policy); two minutes at most
    try { if ((await fetch("api/healthz", {cache: "no-store"})).ok) { await loadGenRestart("Restarted with the saved settings."); $("#genRsHd").focus(); return; } }
    catch { /* not yet */ }
    await new Promise(r => setTimeout(r, 2000));
  }
  sayIn(msg, "The server hasn't come back yet. If it isn't run by Docker with a restart policy, start it again where it runs.", true);
}
