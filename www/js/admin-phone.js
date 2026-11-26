/* Sanguity · Settings → Install → General: Phone receiver (#1003). The upload address (WATCH_UPLOAD_URL), for a
   receiver behind a reverse proxy: the bar's Save asks the server what it changes first (routes_host.py: the address
   phones are given, and how many profiles' phones need the new one), asks when any do, then the fresh check. Beside it,
   read-only (#998: the receiver's settings are its own): its published port, time zone, how long unclaimed data is
   kept and what a restore replaced, each with where it's set. admin.js starts it (adminStart: loadPhone);
   settings-nav.js's bar saves it (ADM: phoneSave, phoneDiscard). */
"use strict";

let RX_PHONE = null, PHONE_REAUTH = null;
const phoneReauth = () => PHONE_REAUTH || (PHONE_REAUTH = el("div", {id: "phoneReauth", class: "reauth", hidden: true}));
const phoneDirty = () => !!RX_PHONE && RX_PHONE.st.can_change && $("#phoneUrl").value.trim() !== RX_PHONE.st.value;

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
  const card = $("#gen-phone"), st = sec.settings.find(s => s.key === "phone.uploadUrl"), locked = !st.can_change;
  RX_PHONE = {sec, st};
  const msg = el("span", {class: "msg set-msg"});
  const box = el("input", {id: "phoneUrl", type: "url", spellcheck: "false", autocapitalize: "none", autocomplete: "off",
    placeholder: "https://health.example.com/ingest", "aria-labelledby": "phoneUrlName", "aria-describedby": "phoneUrlHint phoneUrlNow",
    readonly: locked || null});
  box.value = st.value;
  const row = el("div", {class: "set set-row", "data-key": st.key});
  const mark = () => { const d = phoneDirty(); row.classList.toggle("set-changed", d); d ? card.setAttribute("data-dirty", 1) : card.removeAttribute("data-dirty"); };
  box.oninput = mark;
  row.append(
    el("div", {class: "set-row-lbl"}, el("h3", {class: "sub", id: "phoneUrlName"}, st.label,
      st.from === "environment" ? el("span", {class: "tag", text: "set by the environment"}) : null,
      el("span", {class: "tag set-unsaved", text: "not saved"})),
      el("div", {id: "phoneUrlHint"}, helpNodes(st.help))),
    el("div", {class: "set-row-ctl"}, box,
      el("p", {class: "hint", id: "phoneUrlNow"}, `Phones are given ${h.upload.length === 1 ? "" : "one of "}`,
        ...h.upload.flatMap((a, i) => [i ? ", " : "", el("code", {text: a})]),
        h.phones ? `; ${plural(h.phones, "profile")} ${h.phones === 1 ? "has" : "have"} a phone with an upload secret.` : "."),
      locked ? el("p", {class: "hint", text: `Set by ${st.env} in the server's environment (.env, or the app's settings on TrueNAS or ` +
        "Unraid), so it's changed there."}) : null));
  const ro = (name, where, ...value) => setRow([el("h3", {class: "sub", text: name}), el("p", {class: "hint", text: where})], value);
  const v = n => vars[n] || {}, set = (x, from) => from === "environment" ? `${x} (set by the environment)` : x;
  card.replaceChildren(...present(
    el("div", {class: "set-hd"}, el("h2", {id: "gen-phone-hd", tabindex: -1, text: sec.title})),
    helpNodes(sec.help), msg, phoneReauth(), row,
    ro("Receiver's published port", "Where this machine publishes the receiver, given in the upload address. RECEIVER_PORT in .env (8731 " +
      "unless set) sets both; Watch Receiver Port on TrueNAS; on Unraid both Watch receiver port fields, the same number; on CasaOS " +
      "its compose file, in both places.", set(String(v("RECEIVER_PORT").value), v("RECEIVER_PORT").from)),
    ro("Time zone", "Where the receiver's days start and end for watch and scale readings. TZ in .env (UTC unless set); Timezone " +
      "on TrueNAS, Time zone on Unraid; CasaOS gives its own.", h.tz ? el("code", {text: h.tz}) : "The receiver's own: not seen by the app here."),
    ro("Unclaimed data", "Phone data that no profile or secret names is deleted this long after it's first found. " +
      "SLOT_GRACE_DAYS in .env (7 unless set).", h.slot_grace_days ? `${h.slot_grace_days} days` : "The receiver's own: not seen by the app here."),
    ro("What a restore replaced", "Kept this long after a restore, then deleted, by the app and the receiver alike. " +
      "RESTORE_KEEP_DAYS in .env sets both.", set(`${v("RESTORE_KEEP_DAYS").value} days`, v("RESTORE_KEEP_DAYS").from))));
  if (note) sayIn(msg, note, false, {say: false});
  mark();
}

/* the bar's Save: what it changes, from the server; asked first when phones need the new address */
async function phoneSave(msg) {
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
  $("#gen-phone-hd").focus();
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
