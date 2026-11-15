const btn = $("#b"), err = $("#e");
api("setup").then(r => r.json()).then(s => { if (!s.needed) location.replace("login.html"); }).catch(() => {});
$("#pn").textContent = "At least 15 characters. A few unrelated words work well.";
$("#f").addEventListener("submit", async ev => {
  ev.preventDefault(); err.textContent = "";
  if ($("#p").value !== $("#p2").value) { err.textContent = "The passwords don't match."; return; }
  btn.disabled = true;
  try {
    const r = await api("setup", {method: "POST",
      body: JSON.stringify({code: $("#c").value, username: $("#u").value.trim(), password: $("#p").value, profile_name: $("#n").value.trim() || null})});
    const j = await r.json().catch(() => ({}));
    if (r.ok) { location.replace("./"); return; }
    err.textContent = j.detail ? (Array.isArray(j.detail) ? "Please fill in every field." : j.detail) : "Setup failed (" + r.status + ").";
  } catch (e) { err.textContent = "Couldn't reach the server."; }
  btn.disabled = false;
});
