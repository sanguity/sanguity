/* Sanguity · Settings → Install → General → Demo profile (#1101): the fictional demo as a profile of its own, added
   here (or as the first account is made, setup.html) and removed whole in one step, Remove demo. A fresh install has
   none: it starts empty. GET, POST and DELETE settings/install/demo (server/demo.py); then the top bar's profile menu
   is drawn again, and with the demo shown when it goes, the next profile is (profileGone, profile-card.js). call(),
   ME and PID are settings.js's; forgetProfiles is pwa.js's. */
"use strict";
async function loadGenDemo() {
  const box = $("#genDemo"), msg = $("#genDemoMsg");
  let d;
  try { d = await call("settings/install/demo"); }
  catch (e) { box.replaceChildren(); sayIn(msg, e.message, true); return; }
  const btn = (id, text, onclick, cls = "btn") => el("button", {class: cls, type: "button", id, text, onclick});
  if (!d.profile) {
    box.replaceChildren(setRow(el("div", {}, el("p", {class: "set-row-name", text: "No demo profile"}),
        el("p", {class: "hint", text: d.available ? `Adds a profile named “${d.name}”, yours, with about two years of made-up records.`
          : "This install has no demo generator (demo/generate.py)."})),
      btn("demoAdd", "Add a demo profile", async ev => {
        ev.target.disabled = true;
        try {
          const p = await call("settings/install/demo", "POST");
          await demoMenu();
          await loadGenDemo();
          sayIn(msg, `Added “${p.name}”: it's in the profile menu.`);
          $("#demoOpen")?.focus();
        } catch (e) { ev.target.disabled = false; sayIn(msg, e.message, true); }
      })));
    $("#demoAdd").disabled = !d.available;
    return;
  }
  const p = d.profile;
  box.replaceChildren(setRow(el("div", {}, el("p", {class: "set-row-name"}, "“", el("b", {text: p.name}), "” is on this server"),
      el("p", {class: "hint", text: "Remove demo deletes it and everything in it, for everyone it's shared with. No other profile is touched."})),
    el("div", {class: "form inline"}, el("a", {class: "btn", id: "demoOpen", href: `./?profile=${p.id}`, text: "Open it"}),
     btn("demoRemove", "Remove demo", async () => {
       if (!await ask({title: "Remove the demo profile?", danger: true, action: "Remove demo",
         message: `This deletes “${p.name}”: its made-up records, and anything added to it since. No other profile is touched.`})) return;
       try { await call("settings/install/demo", "DELETE"); }
       catch (e) { sayIn(msg, e.message, true); return; }
       forgetProfiles(id => id === p.id);   // what this browser kept of it (pwa.js)
       if (PID === p.id) await profileGone(); else await demoMenu();
       await loadGenDemo();
       sayIn(msg, `Removed “${p.name}”.`);
       $("#demoAdd")?.focus();
     }, "btn danger"))));
}
/* the profile menu as it is now (nav.js), the profile shown kept */
async function demoMenu() {
  ME = await call("me");
  SgNav.profiles(ME.profiles, PID);
}
