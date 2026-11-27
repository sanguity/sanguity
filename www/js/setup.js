/* Sanguity · first-run setup (setup.html): the setup code, the first account (the admin), and whose records the server
   keeps (the first profile's name, asked here once: the guide counts its first step done, #290). The password has
   Show and a live count toward the server's minimum (pwField, util.js). Refused, focus goes to the field it's about,
   marked invalid, else back on Create account (#498): the field the server names, never one guessed from the message's
   words, and each send clears the marks of the last (#501); the reason under that field, in sight with it and
   describing it (fieldSaid, util.js, #505). The note says it owns the records here only where there are some. A fresh install has none: it starts empty, and
   "Also add a demo profile" adds the fictional demo as a profile of its own (#1101). */
const btn = $("#b"), err = $("#e");
const pw = pwField($("#p"), {min: 15, hint: $("#pn")});
pwField($("#p2"), {name: "repeated password"});   // its Show named apart from the first's (#498)
api("setup").then(r => r.json()).then(s => {
  if (!s.needed) location.replace("login.html");
  else { pw.count(s.password_min); $("#owns").hidden = !s.records; }
}).catch(() => {});
for (const id of ["c", "u", "p", "p2"]) $("#" + id).addEventListener("input", ev => ev.target.removeAttribute("aria-invalid"));
$("#f").addEventListener("submit", async ev => {
  ev.preventDefault();
  fieldSaid(err, "", null, btn);
  if ($("#p").value !== $("#p2").value) {
    fieldSaid(err, "The passwords don't match.", $("#p2"));
    return;   // the passwords left as they were, shown if they were, to see what differs (#492)
  }
  pwSent($("#f"));   // hidden as it's sent, so a browser saves it as a password
  btn.disabled = true;
  let field = null, said;
  try {
    const r = await api("setup", {method: "POST",
      body: JSON.stringify({code: $("#c").value, username: $("#u").value.trim(), password: $("#p").value, profile_name: $("#n").value.trim() || null,
        demo: $("#d").checked})});   // (#1101) a profile of its own, beside theirs
    const j = await r.json().catch(() => ({}));
    if (r.ok) { location.replace("./"); return; }
    said = j.detail ? (Array.isArray(j.detail) ? "Please fill in every field." : j.detail) : "Setup failed (" + r.status + ").";
    // the field it's about: the code (403), else the one the server names (its username or password)
    field = r.status === 403 ? $("#c") : ({username: $("#u"), password: $("#p")})[j.field] || null;
  } catch (e) { said = "Couldn't reach the server."; }
  btn.disabled = false;
  if (!field) btn.focus();
  fieldSaid(err, said, field, btn);
  field?.select();
});
