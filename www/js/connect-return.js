/* Sanguity · /connect/return: where a vendor sends a sign-in back (#32). The session cookie is SameSite=Strict, so
   it isn't sent with the vendor's redirect; this page takes the code and state from its own address, removes them from
   the address bar and history at once, and posts them same-origin, where the server checks the state belongs to this
   session and exchanges the code with the PKCE verifier only it holds. Then it goes on to Connected sources. */
"use strict";
(async function () {
  const p = new URLSearchParams(location.search), say = t => { document.getElementById("say").textContent = t; };
  const body = {code: p.get("code"), state: p.get("state"), error: p.get("error"), error_description: p.get("error_description")};
  try { history.replaceState(null, "", location.pathname); } catch (e) {}
  const next = document.getElementById("next");
  let r, j = {};
  try {
    r = await fetch("../api/connect/return", {method: "POST", cache: "no-store", credentials: "same-origin",
      headers: {"X-Sanguity": "1", "Content-Type": "application/json"}, body: JSON.stringify(body)});
    j = await r.json().catch(() => ({}));
  } catch (e) { say("Couldn't reach the Sanguity server to finish the sign-in. Nothing was connected; start it again."); next.hidden = false; return; }
  if (r.status === 401) { say("You're signed out of Sanguity, so the sign-in can't be finished here. Sign in, then connect the source again."); next.href = "../login.html"; next.textContent = "Sign in"; next.hidden = false; return; }
  if (!r.ok) { say(typeof j.detail === "string" ? j.detail : "The sign-in couldn't be finished. Nothing was connected."); next.hidden = false; return; }
  location.replace(`../connect.html?profile=${encodeURIComponent(j.profile)}&connected=${encodeURIComponent(j.connection)}`);
})();
