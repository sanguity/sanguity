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
let ticket = null;   // after a correct password, when the account has two-factor sign-in on
const pkBtn = document.getElementById("pk"), pkBox = document.getElementById("pkBox");
pkBox.hidden = !passkeysWork();   // only over HTTPS (or localhost), in a browser that has them
function codeStep(on, methods = {code: true}) {
  ticket = on || null;
  const code = !on || methods.code !== false;
  document.getElementById("step1").hidden = !!on; document.getElementById("step2").hidden = !on;
  document.getElementById("codeBox").hidden = !on || !code; btn.hidden = !code;
  document.getElementById("u").readOnly = !!on; document.getElementById("p").required = !on;
  btn.textContent = on ? "Continue" : "Sign in";
  // after the password: a passkey is the other way to finish, if the account has one; before it, one signs in alone
  pkBox.hidden = !passkeysWork() || (on && !methods.passkey);
  document.getElementById("pkOr").hidden = on && !code;
  pkBtn.textContent = on ? "Use a passkey" : "Sign in with a passkey";
  if (on) (code ? document.getElementById("c") : pkBtn).focus();
}
function signedIn(j) {
  if (j.recovery_codes_left != null)   // a recovery code was used: say what that means before going on
    alert(`Signed in with a recovery code. You have ${j.recovery_codes_left} left, and every other device was signed out. ` +
          "If you lost your phone, set up two-factor sign-in again under Account.");
  location.replace(j.must_change_pw ? "account.html#password" : "./");
}
pkBtn.addEventListener("click", async () => {
  pkBtn.disabled = btn.disabled = true; err.textContent = "";
  try {
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
    err.textContent = j.detail || "Sign-in failed (" + r.status + ").";
    if (ticket && /too long. Enter your password/.test(j.detail || "")) { codeStep(null); document.getElementById("p").value = ""; }
  } catch (e) { err.textContent = e.message === "Failed to fetch" ? "Couldn't reach the server." : e.message; }
  pkBtn.disabled = btn.disabled = false;
});
f.addEventListener("submit", async ev => {
  ev.preventDefault();
  const username = document.getElementById("u").value.trim(), password = document.getElementById("p").value;
  btn.disabled = true; err.textContent = "";
  try {
    const r = ticket
      ? await api("login/code", {method: "POST", body: JSON.stringify({ticket, code: document.getElementById("c").value})})
      : await api("login", {method: "POST", body: JSON.stringify({username, password})});
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.two_factor) { codeStep(j.ticket, j.methods); btn.disabled = false; return; }
    if (r.ok) { signedIn(j); return; }
    err.textContent = j.detail || "Sign-in failed (" + r.status + ").";
    if (ticket && /too long/.test(j.detail || "")) { codeStep(null); document.getElementById("p").value = ""; }
  } catch (e) { err.textContent = "Couldn't reach the server."; }
  btn.disabled = false;
  (ticket ? document.getElementById("c") : document.getElementById("p")).select();
});
