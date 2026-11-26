/* Sanguity · Settings → Install → General → Security (#1001): how people sign in to this server, and for how long.
   Requiring two-factor sign-in for everyone (Accounts' before #1001, which links here), as it's switched; who may
   create profiles, as it's picked; the numbers (a sign-in's lengths and idle sign-out, the shortest passwords, a
   one-time password's days, the fresh check's minutes, sign-in's free tries) kept as typed, marked "not saved" and
   counted in the card's data-dirty until the group's bar (sidebar.js, settings-nav.js) saves them (genSecSave) or
   puts them back (genSecDiscard), as the sync defaults' are (admin-sources.js). Each is bounded by the server, never
   more lenient than Sanguity's own default; each change asks for the fresh check, in the card (admin.js fresh), and
   one that ends sign-ins says how many and asks first (the server's 409 confirm_sign_ins). One the server's
   environment sets is shown locked, naming its variable; the Secure cookie and the password checks at once are the
   environment's alone, shown read-only. call(), say(), same() and plural() are settings.js's. */
"use strict";   // $, el, setRow, ask, toast and sayIn come from util.js
let GS = null;   // the Security section as the server last gave it: {settings: [...]}, and the variables
const gsBox = () => $("#genSecReauth");
const gsId = key => "gs-" + key.split(".")[1];
const gsNums = () => GS ? GS.settings.filter(st => st.kind === "number") : [];
const gsTyped = () => gsNums().filter(st => { const i = document.getElementById(gsId(st.key)); return i && !i.disabled && i.value !== String(st.value); });
const gsWhere = env => `Set by ${env} where the server is set up (.env, or the app's settings on TrueNAS or Unraid): change it there, or take it out to change it here.`;

async function loadGenSecurity(note) {
  let d;
  try { d = await call("settings/install"); }
  catch (e) { sayIn($("#genSecMsg"), e.message, true); return; }
  gsDraw(d, note);
}
/* the rows, from the server's answer; typed: the numbers changed and not saved, kept as typed */
function gsDraw(d, note, typed = {}) {
  const body = $("#genSecBody"), back = body.contains(document.activeElement) ? document.activeElement.id : null;
  GS = {...d.sections.find(s => s.id === "security"), vars: d.variables};
  const now = k => GS.settings.find(st => st.key === k).value;   // You → Sign-in and security's lengths, as they are now (account.js)
  ME.session = {days: now("security.sessionDays"), hours_without_2fa: now("security.sessionHours"), idle_minutes: now("security.idleMinutes")};
  sessLasts();
  const tag = st => st.from === "install" && !same(st.value, st.default) ? el("span", {class: "tag", text: "set for this server"}) : null;
  const rows = GS.settings.map(st => {
    const id = gsId(st.key), locked = !st.can_change, help = [st.help, locked ? gsWhere(st.env) : ""].filter(Boolean).join(" ");
    if (st.kind === "choice") {   // who may create profiles: saved as it's picked
      return setRow([el("p", {class: "set-row-name", id: id + "-lbl"}, st.label, tag(st)), el("p", {class: "hint", id: id + "-h", text: help})],
        el("fieldset", {class: "set-radios", "aria-labelledby": id + "-lbl", "aria-describedby": id + "-h"},
          el("div", {class: "seg wrap"}, ...Object.entries(st.choices).map(([v, l]) => el("label", {},
            el("input", {type: "radio", name: id, id: `${id}-${v}`, value: v, checked: v === st.value, disabled: locked,
              onchange: () => gsChoose(st, v)}), l)))), {"data-key": st.key});
    }
    const row = setRow([el("label", {for: id}, st.label, tag(st), el("span", {class: "tag set-unsaved", text: "not saved"})),
      el("p", {class: "hint", id: id + "-h", text: help})], null, {"data-key": st.key});
    const input = el("input", {type: "number", id, step: 1, min: st.min, max: st.max, value: typed[st.key] ?? st.value, class: "set-num",
      disabled: locked, "aria-describedby": id + "-h", oninput: () => { row.classList.toggle("set-changed", input.value !== String(st.value)); gsCount(); }});
    row.querySelector(".set-row-ctl").append(input);
    row.classList.toggle("set-changed", input.value !== String(st.value));
    return row;
  });
  // the environment's alone, each with where it's set (#998)
  const v = GS.vars, secure = {"1": "Always", "0": "Never"}[String(v.COOKIE_SECURE.value).trim()] ||
    `Automatic: Secure when the page is opened over HTTPS${location.protocol === "https:" ? ", as this one is" : ""}`;
  const fixed = (name, says, hint) => setRow([el("p", {class: "set-row-name", text: name}), el("p", {class: "hint", text: hint})], el("p", {text: says}));
  rows.push(fixed("Secure session cookie", secure + ".", "Set by COOKIE_SECURE where the server is set up (.env, the Secure cookie field on TrueNAS or Unraid)."),
    fixed("Password checks at once", `${v.HASH_PARALLEL.value} at a time, each using 64 MiB of memory; more wait their turn.`,
      "Set by HASH_PARALLEL in .env or the app's environment variables, read when the server starts."));
  body.replaceChildren(...rows);
  body.onkeydown = ev => { if (ev.key === "Enter" && ev.target.matches(".set-num")) { ev.preventDefault(); $("#setBar .btn.primary")?.click(); } };   // Enter: the bar's Save (#991)
  gsCount();
  if (note) sayIn($("#genSecMsg"), note, false, {say: false});
  if (back && !body.contains(document.activeElement)) document.getElementById(back)?.focus({preventScroll: true});
}
function gsCount() {
  const n = gsTyped().length, card = $("#gen-security");
  if (n) card.setAttribute("data-dirty", n); else card.removeAttribute("data-dirty");
}
/* one setting to the server, with the fresh check when it's asked; one that would end sign-ins, asked first */
async function gsPut(st, value) {
  const send = confirm => fresh(() => call(`settings/install/${encodeURIComponent(st.key)}`, "PUT", {value, confirm}), gsBox());
  try { return await send(false); }
  catch (e) {
    if (e.detail?.code !== "confirm_sign_ins") throw e;
    const n = e.detail.sessions;
    if (!await ask({title: `End ${plural(n, "sign-in")}?`, danger: true, action: n === 1 ? "End it" : "End them",
        message: [`${n === 1 ? "1 sign-in is" : `${n} sign-ins are`} older, or unused for longer, than this change allows. ` +
          `${n === 1 ? "It ends" : "Each ends"} at its next request, and its person signs in again.`,
          e.detail.yours ? "Yours here is one of them: you'll be signed out too." : ""].filter(Boolean)}))
      throw Object.assign(new Error("Not saved: no sign-in was ended."), {why: "no sign-in was ended."});
    return send(true);
  }
}
async function gsChoose(st, v) {
  try {
    const d = await gsPut(st, v);
    gsDraw(d, "", gsTypedNow());
    const said = `Saved: ${st.choices[v].toLowerCase()} may create profiles.`;
    sayIn($("#genSecMsg"), said, false, {say: false});
    toast(said, {action: "Undo", onAction: () => gsChoose(st, st.value)});
  } catch (e) { gsDraw({sections: [GS], variables: GS.vars}, "", gsTypedNow()); sayIn($("#genSecMsg"), e.message, true); }
  document.getElementById(`${gsId(st.key)}-${v}`)?.focus({preventScroll: true});
}
const gsTypedNow = () => Object.fromEntries(gsTyped().map(st => [st.key, document.getElementById(gsId(st.key)).value]));
/* the group's bar: Save, each number typed in turn; the first refused says why, and the rest stay as typed */
async function genSecSave(msg, had) {
  const todo = gsTyped(), done = [];
  let last = null;
  for (const st of todo) {
    const raw = document.getElementById(gsId(st.key)).value, value = raw === "" ? null : +raw;
    try { last = await gsPut(st, value); }
    catch (e) {
      const keep = gsTypedNow();
      if (last) gsDraw(last, "", keep);
      sayIn(msg, (done.length ? `Saved ${done.map(l => `“${l}”`).join(", ")}. ` : "") + `“${st.label}” isn't saved: ${e.why || e.message}`, true);
      return;
    }
    done.push(st.label);
  }
  if (!last) return;
  const said = `Saved ${done.map(l => `“${l}”`).join(", ")}.`;
  gsDraw(last, said);
  if (had) $("#genSecHd").focus({preventScroll: true});
  toast(said);
}
function genSecDiscard() {
  gsDraw({sections: [GS], variables: GS.vars}, "Changes discarded: back as saved.");
  $("#genSecHd").focus();
}

/* ---------------------------------------------------------------- requiring two-factor sign-in (Accounts' before #1001) */
async function loadRequireTf() {
  const req = $("#reqTf");   // its replies in #reqTfMsg, right under it (#323)
  req.checked = (await call("settings")).require_2fa;
  req.onchange = async () => {
    // turning it on is asked first while some accounts have no second factor (#314): each is then refused on every
    // request until it sets one up (server/main.py, user). Turning it off isn't: it takes nothing away
    if (req.checked) {
      let list;
      try { list = await call("users"); } catch (e) { req.checked = false; say("#reqTfMsg", e.message, true); return; }
      const without = list.filter(u => !u.disabled && !u.two_factor), n = without.length;
      // without one of your own the server refuses it (main.py, change_server_settings): said at once, not asked (#318)
      if (without.some(u => u.id === ME.user.id)) {
        req.checked = false;
        say("#reqTfMsg", ["To require it for everyone, first turn on two-factor sign-in (the authenticator app) or add a passkey " +
          "for your own account, under ", el("a", {href: "#security", text: "Settings → Sign-in and security"}), "."], true);
        return;
      }
      if (n && !await ask({title: "Require two-factor sign-in for everyone?", danger: true, action: "Require it", message: [
          `${n === 1 ? "1 account doesn't" : `${n} accounts don't`} have a second factor yet (an authenticator app or a passkey).`,
          "An account without one can't open any records until it has one: Sanguity asks it to set one up first. " +
          "You can turn this off again at any time."]})) { req.checked = false; return; }
    }
    try {   // (#1006: the fresh check, as every server setting's change)
      await fresh(() => call("settings", "PATCH", {require_2fa: req.checked}), gsBox());
      say("#reqTfMsg", req.checked ? "Everyone now needs two-factor sign-in." : "Two-factor sign-in is now optional.");
    }
    catch (e) { req.checked = !req.checked; say("#reqTfMsg", e.message, true); }
    if (!document.activeElement || document.activeElement === document.body) req.focus({preventScroll: true});   // (the check's form gone)
  };
}
