/* Sanguity · Notifications (#1130; routes_notify.py, notify.py). Two cards:
   - Settings → Install → General: Notifications (#gen-notify, admins): the notification server (the install setting
     notify.url, an ntfy topic's address) and its access token (never shown back: only whether one is saved, or set by
     NOTIFY_TOKEN in the server's environment), kept as typed and marked "not saved" until the group's bar
     (settings-nav.js: notifySave, after the fresh check, in the card) saves them or puts them back (notifyDiscard);
     beside them Test, and the only words a notification says. admin.js starts it (adminStart: loadNotifyServer).
   - Settings → You → Notifications (#notify, everyone): shown once the server is set; each profile this person can
     see, with New results and Dose reminders, each turned on or off at once. account.js starts it (acctStart).
   call() and plural() are settings.js's. */
"use strict";   // $, el, setRow, helpNodes, present, toast and sayIn come from util.js

let NOTIFY = null, NOTIFY_REAUTH = null;
const notifyReauth = () => NOTIFY_REAUTH || (NOTIFY_REAUTH = el("div", {id: "notifyReauth", class: "reauth", hidden: true}));
const notifyUrlDirty = () => !!NOTIFY && NOTIFY.st.can_change && $("#notifyUrl").value.trim() !== NOTIFY.st.value;
const notifyTokenDirty = () => !!NOTIFY && NOTIFY.d.token !== "environment" && $("#notifyToken").value.trim() !== "";

async function loadNotifyServer(note) {
  const card = $("#gen-notify");
  let inst, d;
  try { [inst, d] = await Promise.all([call("settings/install"), call("settings/install/notify")]); }
  catch (e) { card.replaceChildren(el("h2", {text: "Notifications"}), el("p", {class: "msg bad", text: e.message})); return; }
  drawNotifyServer(inst.sections.find(s => s.id === "notify"), d, note);
}

function drawNotifyServer(sec, d, note) {
  const card = $("#gen-notify"), st = sec.settings.find(s => s.key === "notify.url");
  NOTIFY = {sec, st, d};
  const msg = el("span", {class: "msg set-msg"});
  const url = el("input", {id: "notifyUrl", type: "url", spellcheck: "false", autocapitalize: "none", autocomplete: "off",
    placeholder: "https://ntfy.example.com/sanguity", "aria-labelledby": "notifyUrlName", "aria-describedby": "notifyUrlHint"});
  url.value = st.value;
  const tokenHint = d.token === "environment" ? `Set by ${d.token_env} in the server's environment (.env, or the app's settings on TrueNAS or Unraid), so it's changed there.`
    : d.token ? "Saved, encrypted, and never shown again: sent to this server alone. Type a new one to replace it; left empty, it's kept."
    : "Optional: an access token of your ntfy server's, for a topic that isn't open to everyone. Kept for this server alone.";
  const tok = el("input", {id: "notifyToken", type: "password", spellcheck: "false", autocapitalize: "none", autocomplete: "new-password",
    placeholder: d.token ? "Saved" : "", "aria-labelledby": "notifyTokenName", "aria-describedby": "notifyTokenHint",
    readonly: d.token === "environment" || null});
  const urlRow = setRow([el("h3", {class: "sub", id: "notifyUrlName"}, st.label, el("span", {class: "tag set-unsaved", text: "not saved"})),
    el("div", {id: "notifyUrlHint"}, helpNodes(st.help))], url, {class: "set", "data-key": st.key});
  const tokRow = setRow([el("h3", {class: "sub", id: "notifyTokenName"}, "Access token", el("span", {class: "tag set-unsaved", text: "not saved"})),
    el("p", {class: "hint", id: "notifyTokenHint", text: tokenHint})], tok, {class: "set", "data-key": "notify.token"});
  const mark = () => {
    urlRow.classList.toggle("set-changed", notifyUrlDirty()); tokRow.classList.toggle("set-changed", notifyTokenDirty());
    const n = notifyUrlDirty() + notifyTokenDirty();
    n ? card.setAttribute("data-dirty", n) : card.removeAttribute("data-dirty");
  };
  url.oninput = tok.oninput = mark;
  const testMsg = el("span", {class: "msg", role: "status"});
  const test = el("button", {type: "button", class: "btn", text: "Send a test", disabled: !d.url, onclick: async () => {
    test.disabled = true; sayIn(testMsg, "Sending…");
    try { await call("settings/install/notify/test", "POST"); sayIn(testMsg, `Sent to ${d.host}: check that it arrived.`); }
    catch (e) { sayIn(testMsg, e.message, true); }
    test.disabled = false;
    SgOutside.load();   // (each contact is listed there)
  }});
  const ro = (name, hint, ...value) => setRow([el("h3", {class: "sub", text: name}), el("p", {class: "hint", text: hint})], value);
  card.replaceChildren(...present(
    el("div", {class: "set-hd"}, el("h2", {id: "gen-notify-hd", tabindex: -1, text: sec.title})),
    helpNodes(sec.help), msg, notifyReauth(), urlRow, tokRow,
    ro("Test", d.url ? `Sends a test notification to ${d.host} now.` : "Set the notification server, and save it, first.",
      el("div", {class: "row"}, test, testMsg)),
    ro("What each says", (d.click ? `Tapping one opens ${d.click}.` : "Tapping one opens nothing until a public address is set under Host.") +
      ` ${d.people ? `${plural(d.people, "person")} turned some on.` : "Nobody has turned any on yet."}`,
      el("ul", {}, ...d.texts.map(t => el("li", {text: t}))))));
  if (note) sayIn(msg, note, false, {say: false});
  mark();
}

/* the bar's Save: the server's address, then the token, after the fresh check */
async function notifySave(msg, had) {
  if (!notifyUrlDirty() && !notifyTokenDirty()) return;
  const value = $("#notifyUrl").value.trim(), token = $("#notifyToken").value.trim(), saved = [];
  try {
    if (notifyUrlDirty()) {
      await fresh(() => call(`settings/install/${NOTIFY.st.key}`, "PUT", {value, was: NOTIFY.st.value}), notifyReauth());
      saved.push("the notification server");
    }
    if (token && NOTIFY.d.token !== "environment") {
      await fresh(() => call("settings/install/notify/token", "PUT", {token}), notifyReauth());
      saved.push("its access token");
    }
  } catch (e) {
    sayIn(msg, `${saved.length ? "Saved " + saved.join(" and ") + ", but not the rest" : "Notifications aren't saved"}: ${e.message}`, true);
    if (saved.length) await loadNotifyServer();
    return;
  }
  const said = `Saved ${saved.join(" and ")}.`;
  await loadNotifyServer(said);
  if (had) $("#gen-notify-hd").focus();
  toast(said);
  loadNotify();   // (You → Notifications, shown once a server is set)
}
function notifyDiscard() {
  if (!NOTIFY) return;
  drawNotifyServer(NOTIFY.sec, NOTIFY.d, "Changes discarded: back as saved.");
  $("#gen-notify-hd").focus();
}

/* ---------------------------------------------------------------- You → Notifications: this person's choices */
const NOTIFY_KINDS = [["results", "New results", "when a connected portal brings new records"],
  ["doses", "Dose reminders", "at the times of the medication schedules with Remind me on (paused ones don't)"]];

async function loadNotify() {
  const card = $("#notify");
  let d;
  try { d = await call("me/notify"); } catch (e) { card.hidden = true; return; }
  card.hidden = !d.set_up;
  if (!d.set_up) return;
  const msg = el("p", {class: "msg", id: "notifyMsg", role: "status"});
  const rows = d.profiles.map(p => setRow([el("p", {class: "set-row-name", id: "notifyP" + p.id, text: p.name})],
    [el("div", {class: "notify-kinds", role: "group", "aria-labelledby": "notifyP" + p.id}, ...NOTIFY_KINDS.map(([k, label]) => {
      const box = el("input", {type: "checkbox", checked: p[k], "data-kind": k, onchange: async () => {
        box.disabled = true;
        try {
          await call("me/notify", "PUT", {profile: p.id, kind: k, on: box.checked});
          sayIn(msg, `${label} for ${p.name}: ${box.checked ? "on" : "off"}.`);
        } catch (e) { box.checked = !box.checked; sayIn(msg, e.message, true); }
        box.disabled = false;
      }});
      return el("label", {class: "check"}, box, " " + label);
    }))], {"data-profile": p.id}));
  card.replaceChildren(...present(
    el("h2", {id: "notifyHd", tabindex: -1, text: "Notifications"}),
    helpNodes(`Sent to ${d.host}, this server's own notification server, when you turn them on for a profile.\n` +
      "Each says only which profile and what kind, like “Sanguity: new results for Ben”: never a result, a medication " +
      "or anything else of the records. Tapping one opens Sanguity, where you sign in to see what it is. New results: " +
      NOTIFY_KINDS[0][2] + ". Dose reminders: " + NOTIFY_KINDS[1][2] + ". Whoever reads that topic sees them, so ask your admin who does."),
    ...(rows.length ? rows : [emptyState("No profile yet: once one is yours or shared with you, it's listed here.", null, {cls: ""})]), msg));
}
