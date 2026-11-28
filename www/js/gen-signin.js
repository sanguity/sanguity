/* Sanguity · Settings → Install → General: Sign-in provider (#1109). One OpenID Connect provider for the install
   (routes_oidc.py): its issuer, client ID, client secret (never shown back: only whether one is saved, or set by
   OIDC_CLIENT_SECRET in the server's environment), the button's name on the sign-in page, the scopes and the acr that
   counts as two factors, kept as typed and marked "not saved" until the group's bar (settings-nav.js) saves them
   (signinSave, after the fresh check, in the card: admin.js fresh) or puts them back (signinDiscard). Beside them, the
   redirect URI to register, Test connection (the provider's discovery document and keys, read again), and Sign in with
   OIDC only, switched at once, offered once this admin's own identity there has signed in. admin.js starts it
   (adminStart: loadSignin). call(), say() and plural() are settings.js's. */
"use strict";   // $, el, setRow, helpNodes, present, ask, toast and sayIn come from util.js

let SIGNIN = null, SIGNIN_REAUTH = null;
const signinReauth = () => SIGNIN_REAUTH || (SIGNIN_REAUTH = el("div", {id: "signinReauth", class: "reauth", hidden: true}));
const SIGNIN_FIELDS = [
  ["issuer", "Issuer", "url", "https://auth.example.com/realms/home",
    "The provider's issuer address, exactly as it shows it (with its last slash, if it has one). https:// only."],
  ["client_id", "Client ID", "text", "sanguity", "The ID the provider gave Sanguity's app (its client)."],
  ["secret", "Client secret", "password", "", ""],
  ["label", "Button's name", "text", "single sign-on", "On the sign-in page: Sign in with this name."],
  ["scopes", "Scopes", "text", "openid email profile", "What's asked for, with spaces between: openid is needed; email and profile give the name shown for a linked identity."],
  ["acr", "acr that counts as two factors", "text", "", "Optional. A sign-in whose ID token has this acr counts as two factors, as one whose amr says mfa, or names " +
    "methods of two factors (pwd and otp, say), does; any other is followed by the account's own code or passkey, where it has one."]];
const signinIn = k => document.getElementById("signin-" + k);
const signinTyped = () => SIGNIN ? SIGNIN_FIELDS.filter(([k]) => { const i = signinIn(k); return i && !i.readOnly && i.value.trim() !== (k === "secret" ? "" : String(SIGNIN.d[k] ?? "")); }) : [];

async function loadSignin(note) {
  const card = $("#gen-signin");
  let d;
  try { d = await call("settings/install/oidc"); }
  catch (e) { card.replaceChildren(el("h2", {text: "Sign-in provider"}), el("p", {class: "msg bad", text: e.message})); return; }
  drawSignin(d, note);
}

function drawSignin(d, note) {
  const card = $("#gen-signin"), locked = d.only;
  SIGNIN = {d};
  const msg = el("span", {class: "msg set-msg"});
  const mark = () => { const n = signinTyped().length; for (const [k] of SIGNIN_FIELDS) signinIn(k)?.closest(".set-row").classList.toggle("set-changed", signinTyped().some(([t]) => t === k));
    n ? card.setAttribute("data-dirty", n) : card.removeAttribute("data-dirty"); };
  const secretHint = d.secret === "environment" ? `Set by ${d.secret_env} in the server's environment (.env, or the app's settings on TrueNAS or Unraid), so it's changed there.`
    : d.secret ? "Saved, encrypted, and never shown again. Type a new one to replace it; left empty, it's kept."
    : "None saved: Sanguity signs in as a public client, with PKCE alone. Type the provider's secret for a confidential one.";
  const rows = SIGNIN_FIELDS.map(([k, label, type, placeholder, hint]) => {
    const id = "signin-" + k, fixed = (locked && k !== "label") || (k === "secret" && d.secret === "environment");
    const input = el("input", {id, type, spellcheck: "false", autocapitalize: "none", autocomplete: k === "secret" ? "new-password" : "off",
      placeholder: k === "secret" && d.secret ? "Saved" : placeholder, "aria-describedby": id + "-h", readonly: fixed || null, oninput: mark});
    input.value = k === "secret" ? "" : d[k] ?? "";
    const says = [k === "secret" ? secretHint : hint, fixed && locked && k !== "secret" ? "Turn off Sign in with OIDC only, below, to change it." : ""].filter(Boolean).join(" ");
    return setRow([el("label", {for: id}, label, el("span", {class: "tag set-unsaved", text: "not saved"})), el("p", {class: "hint", id: id + "-h", text: says})],
      input, {class: "set", "data-key": "oidc." + k});
  });
  const ro = (name, hint, ...value) => setRow([el("h3", {class: "sub", text: name}), el("p", {class: "hint", text: hint})], value);
  const testMsg = el("span", {class: "msg", role: "status"});
  const test = el("button", {type: "button", class: "btn", text: "Test connection", disabled: !d.configured, onclick: async () => {
    test.disabled = true; sayIn(testMsg, "Asking the provider…");
    try {
      const r = await call("settings/install/oidc/test", "POST");
      sayIn(testMsg, `Reached it: its discovery document names ${r.issuer}, with ${plural(r.keys, "signing key")} Sanguity can check its ID tokens with.`);
    } catch (e) { sayIn(testMsg, e.message, true); }
    test.disabled = false;
    SgOutside.load();   // (each contact is listed there)
  }});
  const only = el("input", {type: "checkbox", role: "switch", id: "signinOnly", checked: d.only, "aria-describedby": "signinOnlyH signinOnlyMsg",
    disabled: !d.configured || (!d.only && !d.may_go_only), onchange: () => signinOnly(only)});
  const onlyHint = `Passwords, and passkeys on their own, stop signing in: everyone signs in with ${d.label}, then with their own code or passkey where ` +
    "it asks. " + (d.only || d.may_go_only ? "" : "Offered once your own identity there has signed in: link it under Sign-in and security. ") +
    "If the provider stops working, python -m server.manage oidc-only off turns this back.";
  card.replaceChildren(...present(
    el("div", {class: "set-hd"}, el("h2", {id: "gen-signin-hd", tabindex: -1, text: "Sign-in provider"})),
    // (its first line the hint, the rest behind the heading's ⓘ: one line at 1400 px, as every card's)
    helpNodes("One OpenID Connect provider, to sign in with on the sign-in page.\nLike Authentik, Keycloak, Authelia or Google: " +
      "each person who links their identity there signs in with it. Nobody is contacted until it's set up. Each change applies at once."),
    msg, signinReauth(), ...rows,
    ro("Redirect URI", d.from_public_address ? "Register exactly this with the provider, as the app's redirect URI (or callback URL). It's made from the address the app gives out, under Host."
      : "Register exactly this with the provider, as the app's redirect URI (or callback URL). It's this page's address: set a public address under Host so it stays the same.",
      el("p", {}, el("code", {text: d.redirect_uri || "none"}))),
    ro("Test connection", d.configured ? `Reads the provider's discovery document and signing keys, from ${d.hosts.join(", ")}. ${d.linked === 1 ? "1 identity" : `${d.linked} identities`} linked.`
      : "Set the issuer and the client ID, and save them, first.", el("div", {class: "row"}, test, testMsg)),
    setRow([el("p", {class: "set-row-name", id: "signinOnlyL"}, "Sign in with OIDC only"), el("p", {class: "hint", id: "signinOnlyH", text: onlyHint})],
      [el("label", {class: "check switch"}, only, ` Sign in with ${d.label} only`), el("p", {class: "msg", id: "signinOnlyMsg", role: "status"})])));
  if (note) sayIn(msg, note, false, {say: false});
  mark();
}

/* the bar's Save: what's typed, after the fresh check */
async function signinSave(msg, had) {
  const typed = signinTyped();
  if (!typed.length) return;
  const body = Object.fromEntries(typed.map(([k]) => [k, signinIn(k).value.trim()]));
  try { await fresh(() => call("settings/install/oidc", "PUT", body), signinReauth()); }
  catch (e) { sayIn(msg, `The sign-in provider isn't saved: ${e.message}`, true); return; }
  await loadSignin("Saved the sign-in provider.");
  if (had) $("#gen-signin-hd").focus();
  toast("Saved the sign-in provider.");
  ME.sign_in_provider = SIGNIN.d.configured ? {label: SIGNIN.d.label, only: SIGNIN.d.only} : null;
  if (typeof loadOidc === "function") loadOidc();   // (You → Sign-in and security's card, account.js)
}
function signinDiscard() {
  if (!SIGNIN) return;
  drawSignin(SIGNIN.d, "Changes discarded: back as saved.");
  $("#gen-signin-hd").focus();
}
/* Sign in with OIDC only: switched at once, after the fresh check; turning it on is asked first */
async function signinOnly(box) {
  const on = box.checked, m = $("#signinOnlyMsg");
  if (on && !await ask({title: `Sign in with ${SIGNIN.d.label} only?`, danger: true, action: "Turn it on", message: [
      "Passwords, and passkeys on their own, stop signing in for every account. Someone whose identity there isn't linked can't sign in until it is.",
      "If the provider stops working, python -m server.manage oidc-only off, on the server, turns this back."]})) { box.checked = false; return; }
  try { await fresh(() => call("settings/install/oidc", "PUT", {only: on}), signinReauth()); }
  catch (e) { box.checked = !on; sayIn(m, e.message, true); return; }
  await loadSignin();
  $("#signinOnly").focus();
  sayIn($("#signinOnlyMsg"), on ? `Only ${SIGNIN.d.label} signs in now.` : "Passwords and passkeys sign in again.");
}
