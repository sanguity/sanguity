const f = document.getElementById("f"), btn = document.getElementById("b"), err = document.getElementById("e");
api("setup").then(r => r.json()).then(s => { if (s.needed) location.replace("setup.html"); else stayNote(s.session); }).catch(() => {});
function stayNote(t) {   // how long a sign-in lasts, as this server is set up (SESSION_* in .env)
  if (!t) return;
  const n = (v, unit) => `${v} ${unit}${v === 1 ? "" : "s"}`;
  let say = "Private medical records. You'll stay signed in on this device until you log out";
  say += t.hours_without_2fa && t.hours_without_2fa < t.days * 24
    ? `: up to ${n(t.days, "day")} with two-factor sign-in or a passkey, otherwise ${n(t.hours_without_2fa, "hour")}.`
    : `, for up to ${n(t.days, "day")}.`;
  if (t.idle_minutes) say += ` Leave it unused for ${n(t.idle_minutes, "minute")} and you'll be signed out.`;
  document.getElementById("stay").textContent = say;
}
{ const w = plainHttpWarning(), box = document.getElementById("insecure"); if (w) { box.textContent = w; box.hidden = false; } }
/* a Log out made without the server (#422, pwa.js SgLogout): this device's part is done; the sign-in on the server is
   ended here as soon as the server answers (tried at once, when the browser says it's online, and every few seconds,
   as that isn't always said), and the page says which has happened. Signing in waits for that, so it can never end
   the new sign-in. */
const settled = async () => !SgLogout.due() || await SgLogout.finish();
if (SgLogout.due()) {
  const out = el("p", {class: "note", id: "outNote"});
  f.querySelector(".logo").after(out);
  sayIn(out, "Signed out on this device, and what it kept for offline is deleted. Your sign-in on the server ends once " +
    "this device reaches it again; until then, Sanguity here opens on this page.");
  let trying = false, done = false;
  const end = async () => {   // (ended from another tab's sign-in page, #429: said here too)
    if (trying || done) return;
    trying = true;
    if (!SgLogout.due() || await SgLogout.finish()) { done = true; clearInterval(again); sayIn(out, "Signed out. Your sign-in on the server has ended too."); }
    trying = false;
  };
  const again = setInterval(end, 4000);
  end();
  addEventListener("online", end);
  addEventListener("storage", e => { if (e.key === SgLogout.KEY) end(); });
}
pwField(document.getElementById("p"));   // Show and Hide (util.js, #290)
for (const id of ["p", "c"]) document.getElementById(id).addEventListener("input", ev => ev.target.removeAttribute("aria-invalid"));   // (#505)
let ticket = null;   // after a correct password, when the account has two-factor sign-in on
const pkBtn = document.getElementById("pk"), pkBox = document.getElementById("pkBox");
pkBox.hidden = !passkeysWork();   // only over HTTPS (or localhost), in a browser that has them
function codeStep(on, methods = {code: true}) {
  ticket = on || null;
  const code = !on || methods.code !== false, sites = methods.sites || [];
  // #540: none of the account's passkeys made at this name: said, with where they were, and no passkey button (#543:
  // for an account with a code too, whose "Use a passkey" was refused)
  const away = !!on && passkeysWork() && sites.length > 0 && !sites.includes(location.hostname);
  document.getElementById("step1").hidden = !!on; document.getElementById("step2").hidden = !on;
  document.getElementById("codeBox").hidden = !on || !code; btn.hidden = !code;
  // #928: the account's codes stopped after too many wrong ones: said under the box, which takes a recovery code still
  document.getElementById("cn").textContent = on && methods.codes_stopped
    ? "Codes from your authenticator app are stopped for this account: too many wrong ones were entered. Enter one of your recovery codes" +
      (methods.passkey ? ", or use a passkey." : ".")
    : "Lost your phone? Enter one of your recovery codes instead.";
  document.getElementById("u").readOnly = !!on; document.getElementById("p").required = !on;
  btn.textContent = on ? "Continue" : "Sign in";
  // after the password: a passkey is the other way to finish, if the account has one; before it, one signs in alone
  pkBox.hidden = !passkeysWork() || (on && !methods.passkey) || away;
  document.getElementById("pkOr").hidden = on && !code;
  pkBtn.textContent = on ? "Use a passkey" : "Sign in with a passkey";
  // a passkey the only way on: what's needed said, and the button described by it (#518: it said "Use a passkey" alone)
  const says = document.getElementById("pkSays"), pkOnly = !!on && !code;
  says.hidden = !pkOnly && !away;
  if (pkOnly) pkBtn.setAttribute("aria-describedby", says.id); else pkBtn.removeAttribute("aria-describedby");
  // #523: where passkeys can't work (plain HTTP away from localhost, or a browser without them) there's no button to
  // press: the step says why and how to sign in instead, and takes focus itself
  const stuck = pkOnly && (!passkeysWork() || away);
  if (stuck) says.tabIndex = -1; else says.removeAttribute("tabindex");
  // this computer's own address (#531): where the account's passkeys were made, as links (#535: localhost, though they
  // were made at another name), else localhost, which works without HTTPS; a name's port isn't kept (passkeys.py where).
  // localhost a link only from a page on this computer (#543): elsewhere (another device, a proxy on 443) the page's
  // scheme and port aren't the server's, and localhost is the device itself
  const own = /^(127\.|\[::1\]$)/.test(location.hostname), port = location.port ? ":" + location.port : "";
  const loop = own || /(^|\.)localhost$/.test(location.hostname);
  const go = (sites.length ? sites : ["localhost"]).flatMap((h, i) => {
    const lh = /(^|\.)localhost$/.test(h), at = lh ? `${location.protocol}//${h}${port}` : `https://${h}`;
    return [i ? " or " : "", lh && !loop ? `${h} (on the computer it runs on)` : el("a", {href: at + location.pathname, text: at})];
  });
  // with a code (#543): beside its box, which the code's field is described by
  document.getElementById("c").setAttribute("aria-describedby", away && code ? "cn pkSays" : "cn");
  sayIn(says, away && code ? ["None of this account's passkeys was made at this address. To use one, open Sanguity at ", ...go, " and sign in there."] :
    !pkOnly ? "" : !stuck ? "Password accepted. Use your passkey to finish signing in." :
    away ? ["Password accepted, but this account finishes signing in with a passkey, and none of its passkeys was made at this address. Open Sanguity at ",
      ...go, " and sign in there, or use another account."] :
    // on plain HTTP or at an IP (#527), where the account's passkeys were made (#549: "its https:// address" or name)
    ["Password accepted, but this account finishes signing in with a passkey, and passkeys don't work here: ",
      ...(window.isSecureContext && !ipHost() ? ["this browser doesn't have them. Sign in with one that does"]
        : [!window.isSecureContext ? "this page isn't using HTTPS" : "this is an IP address", ". Open Sanguity at ", ...go, " and sign in there"]),
      ", or use another account."],
    false, {say: pkOnly && !stuck});   // (read as it, or the code's field, takes focus)
  if (on) (code ? document.getElementById("c") : stuck ? says : pkBtn).focus();
}
/* Use another account (#518): back to the first step, its username and password empty, from the second step */
document.getElementById("other").addEventListener("click", ev => {
  ev.preventDefault();
  codeStep(null);
  for (const id of ["u", "p", "c"]) document.getElementById(id).value = "";
  fieldSaid(err, "", null, pkBox);
  document.getElementById("u").focus();
});
function signedIn(j) {
  // the account's own theme, remembered before the first page draws (theme.js), as signing out took the last one's
  // (#433): asked here, at most half a second, so a dark account's first page doesn't start light (#436)
  const themed = Promise.race([api("settings/user").then(r => r.ok ? r.json() : null).then(s => s && applyTheme(s.theme)).catch(() => {}),
    new Promise(r => setTimeout(r, 500))]);
  const next = () => themed.then(() => location.replace(j.must_change_pw ? "settings.html#password" : "./"));
  if (j.recovery_codes_left == null) { next(); return; }
  // a recovery code was used: say what that means before going on, on the page with a Continue (#210: not a pop-up)
  const note = el("p", {id: "recoveryNote"}), go = el("button", {type: "button", class: "btn primary large", id: "recoveryGo", text: "Continue", onclick: next});
  f.replaceChildren(f.querySelector(".logo"), note, go);
  sayIn(note, `Signed in with a recovery code. You have ${j.recovery_codes_left} left, and every other device was signed out. ` +
              "If you lost your phone, set up two-factor sign-in again under Settings, Sign-in and security.");
  go.focus();
}
pkBtn.addEventListener("click", async () => {
  pkBtn.disabled = btn.disabled = true; fieldSaid(err, "", null, pkBox);
  let said = "", again = null;
  try {
    if (!await settled()) throw new Error("Couldn't reach the server.");
    let r = await api("login/passkey/options", {method: "POST", body: JSON.stringify({ticket})});
    let j = await r.json().catch(() => ({}));
    if (r.ok) {
      const rpId = j.options.rpId;
      let credential;
      try { credential = await passkeyGet(j.options); } catch (e) { throw new Error(passkeyProblem(e)); }
      r = await api("login/passkey", {method: "POST", body: JSON.stringify({state: j.state, credential, ticket})});
      j = await r.json().catch(() => ({}));
      if (r.ok) { signedIn(j); return; }
      if (/isn't registered here/.test(j.detail || "")) passkeySignalUnknown(rpId, credential.id);   // the device may delete it
    }
    said = j.detail || "Sign-in failed (" + r.status + ").";
    if (ticket && /too long. Enter your password/.test(j.detail || "")) { codeStep(null); again = document.getElementById("p"); again.value = ""; }
  } catch (e) { said = e.message === "Failed to fetch" ? "Couldn't reach the server." : e.message; }
  pkBtn.disabled = btn.disabled = false;
  // the second step timed out: back to the password, which takes focus with the reason under it, as Sign in's (#514)
  if (again) { fieldSaid(err, said, again, pkBox); return; }
  // focus back on the button (#509: disabled while it asked, it dropped focus to the page), its reason in sight under it
  pkBtn.focus({preventScroll: true});
  fieldSaid(err, said, null, pkBox);
  pkBtn.scrollIntoView({block: "nearest"});
});
f.addEventListener("submit", async ev => {
  ev.preventDefault();
  // a passkey the only way on (#523): Enter in the username sent the hidden Continue's empty code, refused as too late
  if (ticket && document.getElementById("codeBox").hidden) return;
  const username = document.getElementById("u").value.trim(), password = document.getElementById("p").value;
  btn.disabled = true; fieldSaid(err, "", null, pkBox); pwSent(f);   // hidden as it's sent, so a browser saves it as a password
  let said = "", refused = false;
  try {
    if (!await settled()) throw new Error("not reached");   // said as the server not reached, below
    const r = ticket
      ? await api("login/code", {method: "POST", body: JSON.stringify({ticket, code: document.getElementById("c").value})})
      : await api("login", {method: "POST", body: JSON.stringify({username, password})});
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.two_factor) { codeStep(j.ticket, j.methods); btn.disabled = false; return; }
    if (r.ok) { signedIn(j); return; }
    said = j.detail || "Sign-in failed (" + r.status + ").";
    refused = r.status === 401;   // the password or the code wrong: the reason under it, in sight with it (#505)
    if (ticket && /too long/.test(j.detail || "")) { codeStep(null); document.getElementById("p").value = ""; }
  } catch (e) { said = "Couldn't reach the server."; }
  btn.disabled = false;
  const field = ticket ? document.getElementById("c") : document.getElementById("p");
  field.select();
  fieldSaid(err, said, refused ? field : null, pkBox);
});
