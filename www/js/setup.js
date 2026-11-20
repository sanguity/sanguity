/* Sanguity · first-run setup (setup.html): the setup code, the first account (the admin), and whose records the server
   keeps (the first profile's name, asked here once: the guide counts its first step done, #290). The password has
   Show and a live count toward the server's minimum (pwField, util.js). */
const btn = $("#b"), err = $("#e");
const pw = pwField($("#p"), {min: 15, hint: $("#pn")});
pwField($("#p2"));
api("setup").then(r => r.json()).then(s => { if (!s.needed) location.replace("login.html"); else pw.count(s.password_min); }).catch(() => {});
$("#p2").addEventListener("input", () => $("#p2").removeAttribute("aria-invalid"));
$("#f").addEventListener("submit", async ev => {
  ev.preventDefault(); err.textContent = "";
  if ($("#p").value !== $("#p2").value) {
    err.textContent = "The passwords don't match.";
    $("#p2").setAttribute("aria-invalid", "true");
    $("#p2").focus();
    return;
  }
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
