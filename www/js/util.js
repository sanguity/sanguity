/* Sanguity · helpers shared by every page: $, el (build DOM), api (call the backend).
   Plain script, loaded first; all files share one global scope. */
"use strict";
const $ = s => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = tag.startsWith("svg:") ? document.createElementNS("http://www.w3.org/2000/svg", tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "text") e.textContent = v;
    else if (k === "class") e.setAttribute("class", v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
};
/* every backend call: same-origin cookie, never cached, and the X-Sanguity header the API requires on changes */
const api = (path, opts = {}) => fetch("api/" + path, {cache: "no-store", credentials: "same-origin", ...opts,
  headers: {...(opts.method && opts.method !== "GET" ? {"X-Sanguity": "1", "Content-Type": "application/json"} : {}), ...opts.headers}});

/* passkeys (WebAuthn): the server's JSON options in, the browser's answer back out as JSON (base64url) */
const b64u = {
  dec: s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), c => c.charCodeAt(0)).buffer,
  enc: b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""),
};
const passkeysWork = () => !!(window.PublicKeyCredential && navigator.credentials && window.isSecureContext);
const _creds = list => (list || []).map(c => ({...c, id: b64u.dec(c.id)}));
async function passkeyCreate(o) {
  const c = await navigator.credentials.create({publicKey: {...o, challenge: b64u.dec(o.challenge),
    user: {...o.user, id: b64u.dec(o.user.id)}, excludeCredentials: _creds(o.excludeCredentials)}});
  return {id: c.id, rawId: b64u.enc(c.rawId), type: c.type, clientExtensionResults: c.getClientExtensionResults(),
    response: {clientDataJSON: b64u.enc(c.response.clientDataJSON), attestationObject: b64u.enc(c.response.attestationObject),
      transports: c.response.getTransports ? c.response.getTransports() : []}};
}
async function passkeyGet(o) {
  const c = await navigator.credentials.get({publicKey: {...o, challenge: b64u.dec(o.challenge), allowCredentials: _creds(o.allowCredentials)}});
  const r = c.response;
  return {id: c.id, rawId: b64u.enc(c.rawId), type: c.type, clientExtensionResults: c.getClientExtensionResults(),
    response: {clientDataJSON: b64u.enc(r.clientDataJSON), authenticatorData: b64u.enc(r.authenticatorData),
      signature: b64u.enc(r.signature), userHandle: r.userHandle && r.userHandle.byteLength ? b64u.enc(r.userHandle) : null}};
}
/* Tell the device which passkeys Sanguity no longer accepts, so it can delete them (the WebAuthn Signal API;
   browsers without it just skip this). */
function passkeySignalUnknown(rpId, credentialId) {
  try { if (PublicKeyCredential.signalUnknownCredential) PublicKeyCredential.signalUnknownCredential({rpId, credentialId}).catch(() => {}); } catch (e) {}
}
function passkeySignalAccepted(rpId, userId, ids) {
  try { if (PublicKeyCredential.signalAllAcceptedCredentials) PublicKeyCredential.signalAllAcceptedCredentials({rpId, userId, allAcceptedCredentialIds: ids}).catch(() => {}); } catch (e) {}
}
/* the browser's refusal, in words: cancelled, timed out, or not allowed here */
const passkeyProblem = e => e && e.name === "NotAllowedError" ? "The passkey request was cancelled or timed out."
  : e && e.name === "InvalidStateError" ? "This device already has a passkey for your account."
  : e && e.name === "SecurityError" ? "Passkeys don't work at this address. Open Sanguity at its https:// name."
  : (e && e.message) || "The passkey didn't work.";
