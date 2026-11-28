/* Sanguity · Settings → Install → General: Phone receiver (#1003). The upload address (WATCH_UPLOAD_URL), for a
   receiver behind a reverse proxy: the bar's Save asks the server what it changes first (routes_host.py: the address
   phones are given, and how many profiles' phones need the new one), asks when any do, then the fresh check. Beside it,
   read-only (#998: the receiver's settings are its own): its published port, time zone, how long unclaimed data is
   kept and what a restore replaced, each with where it's set (util.js factRow: the last three under Needs a restart,
   #1121); the published port is the container's (util.js factRow with its variable, #1158). admin.js starts it (adminStart: loadPhone);
   settings-nav.js's bar saves it (ADM: phoneSave, phoneDiscard). */
"use strict";

let RX_PHONE = null, PHONE_REAUTH = null;
const phoneReauth = () => PHONE_REAUTH || (PHONE_REAUTH = el("div", {id: "phoneReauth", class: "reauth", hidden: true}));
const phoneDirty = () => !!RX_PHONE && $("#phoneUrl").value.trim() !== RX_PHONE.st.value;

async function loadPhone(note) {
  const card = $("#gen-phone");
  let d, h;
  try { [d, h] = await Promise.all([call("settings/install"), call("settings/phone")]); }
  catch (e) { card.replaceChildren(el("h2", {text: "Phone receiver"}), el("p", {class: "msg bad", text: e.message})); return; }
  const sec = d.sections.find(s => s.id === "phone");
  if (!sec) { card.hidden = true; return; }
  drawPhone(sec, d.variables, h, note);
}

function drawPhone(sec, vars, h, note) {
  const card = $("#gen-phone"), st = sec.settings.find(s => s.key === "phone.uploadUrl");
  RX_PHONE = {sec, st};
  const msg = el("span", {class: "msg set-msg"});
  const now = el("p", {class: "hint", id: "phoneUrlNow"}, `Phones are given ${h.upload.length === 1 ? "" : "one of "}`,
    ...h.upload.flatMap((a, i) => [i ? ", " : "", el("code", {text: a})]),
    h.phones ? `; ${plural(h.phones, "profile")} ${h.phones === 1 ? "has" : "have"} a phone with an upload secret.` : ".");
  let row;
  {
    const box = el("input", {id: "phoneUrl", type: "url", spellcheck: "false", autocapitalize: "none", autocomplete: "off",
      placeholder: "https://health.example.com/ingest", "aria-labelledby": "phoneUrlName", "aria-describedby": "phoneUrlHint phoneUrlNow"});
    box.value = st.value;
    row = el("div", {class: "set set-row", "data-key": st.key});
    box.oninput = () => mark();
    row.append(
      el("div", {class: "set-row-lbl"}, el("h3", {class: "sub", id: "phoneUrlName"}, st.label, el("span", {class: "tag set-unsaved", text: "not saved"})),
        el("div", {id: "phoneUrlHint"}, helpNodes(st.help))),
      el("div", {class: "set-row-ctl"}, box, now));
  }
  const mark = () => { const d = phoneDirty(); row.classList.toggle("set-changed", d); d ? card.setAttribute("data-dirty", 1) : card.removeAttribute("data-dirty"); };
  const v = n => vars[n] || {};
  // (#1119) the receiver's time zone and days, and what a restore replaced, are changed under Needs a restart (#1121):
  // each points there (#1158: the environment sets none of them)
  const there = n => [el("a", {href: "#gen-restart", text: "Needs a restart"}), `, below (${n})`];
  card.replaceChildren(...present(
    el("div", {class: "set-hd"}, el("h2", {id: "gen-phone-hd", tabindex: -1, text: sec.title})),
    helpNodes(sec.help), msg, phoneReauth(), row,
    factRow("Receiver's published port", "Where this machine publishes the receiver, given in the upload address.", String(v("RECEIVER_PORT").value),
      "RECEIVER_PORT in .env (8731 unless set) sets both; Watch Receiver Port on TrueNAS; on Unraid both Watch receiver port fields, the same " +
      "number; on CasaOS its compose file, in both places", v("RECEIVER_PORT").from === "environment" ? "RECEIVER_PORT" : null),
    factRow("Time zone", "Where the receiver's days start and end for watch and scale readings.", h.tz, there("TZ")),
    factRow("Unclaimed data", "Phone data that no profile or secret names is deleted this long after it's first found.", plural(h.slot_grace_days, "day"),
      there("SLOT_GRACE_DAYS")),
    factRow("What a restore replaced", "Kept this long after a restore, then deleted, by the app and the receiver alike.",
      `${v("RESTORE_KEEP_DAYS").value} days`, there("RESTORE_KEEP_DAYS"))));
  if (note) sayIn(msg, note, false, {say: false});
  mark();
}

/* the bar's Save: what it changes, from the server; asked first when phones need the new address */
async function phoneSave(msg, had) {
  if (!phoneDirty()) return;
  const k = RX_PHONE.st.key, was = RX_PHONE.st.value;
  let e;
  try { e = await call(`settings/install/${k}/effects`, "POST", {value: $("#phoneUrl").value}); }
  catch (err) { sayIn(msg, `The upload address isn't saved: ${err.message}`, true); return; }
  if (e.phones && !await ask({title: "Change the upload address?", action: "Change it",
      message: `The phones of ${plural(e.phones, "profile")} with an upload secret need the new address, ${e.upload[1].join(", ")}, in their ` +
        "phone app: until then, what they send doesn't arrive."})) {
    sayIn(msg, "The upload address isn't saved.");
    return;
  }
  try { await fresh(() => call(`settings/install/${k}`, "PUT", {value: e.value, was}), phoneReauth()); }
  catch (err) { sayIn(msg, `The upload address isn't saved: ${err.message}`, true); return; }
  await loadPhone("Saved the upload address.");
  if (had) $("#gen-phone-hd").focus();   // (not while another part waits on its confirmation, #1049)
  toast("Saved the upload address.");
}
function phoneDiscard() {
  if (!RX_PHONE) return;
  $("#phoneUrl").value = RX_PHONE.st.value;
  $("#gen-phone .set-row").classList.remove("set-changed");
  $("#gen-phone").removeAttribute("data-dirty");
  sayIn($("#gen-phone .set-msg"), "Changes discarded: back as saved.");
  $("#gen-phone-hd").focus();
}
