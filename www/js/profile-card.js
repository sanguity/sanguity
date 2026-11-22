/* Sanguity · Settings → This profile → Rename or delete (#402; under Sharing → Profile before): the profile's name and
   deleting it, for its owners only (as the server allows: PATCH and DELETE profiles/{id}); no one else is shown the
   group. A rename has an Undo (#272) that puts the name before back, if it's still the one given here. Deleting can't
   be undone, so it's in the danger zone (util.js, #287) behind "Delete profile…", offers Export everything first, and goes ahead only once the profile's
   name is typed exactly (the server checks it again, confirm_name); then the next profile is shown, or with none
   left, only your own settings, as for someone who has no profile yet. drawProfile(p), from settings.js as a profile
   is shown; call(), load(), ME and PID are settings.js's. Plain script; all files share one scope. */
"use strict";
function drawProfile(p) {
  const card = $("#profileCard"), msg = $("#profileMsg"), fail = e => sayIn(msg, e.message, true);
  sayIn(msg, "");
  card.hidden = p.role !== "owner";
  if (card.hidden) { $("#profileBody").replaceChildren(); return; }
  const name = el("input", {value: p.name, maxlength: 80, id: "profileName"});   // ids: focus back after a rename (#409)
  $("#profileBody").replaceChildren(
    // its label shown (#419): the card's own "Name" is said only, under the group's title, while it's alone there; a
    // setting row each (#286, util.js), the name and deleting it
    setRow(el("label", {for: "profileName", text: "Profile name"}), el("form", {class: "form inline", onsubmit: async ev => { ev.preventDefault();
      const was = p.name, to = name.value.trim();
      if (to === was) return;
      const had = document.activeElement?.id;
      try { await call("profiles/" + p.id, "PATCH", {name: to}); await profileNamed(p.id); } catch (e) { fail(e); return; }
      if (had) document.getElementById(had)?.focus({preventScroll: true});
      sayIn($("#profileMsg"), `Renamed to “${to}”.`, false, {say: false});
      toast(`Renamed “${was}” to “${to}”.`, {action: "Undo", onAction: async () => {
        const now = (await call("me")).profiles.find(x => x.id === p.id), back = !!now && now.name === to;
        if (back) await call("profiles/" + p.id, "PATCH", {name: was});
        const text = back ? `Undone: the profile is “${was}” again.` : "The profile was renamed since, so its name is left as it is.";
        if (await profileNamed(p.id)) sayIn($("#profileMsg"), text); else toast(text);   // another profile shown now (#367)
      }}); }},
      name, el("button", {class: "btn", type: "submit", id: "profileRename", text: "Rename"}))),
    profileDelete(p));
}
/* the names as they are now: in the top bar's profile menu, and in this card while it's pid's. -> whether it is */
async function profileNamed(pid) {
  ME = await call("me");
  SgNav.profiles(ME.profiles, PID);
  const p = ME.profiles.find(x => x.id === pid);
  if (!p || PID !== pid) return false;
  drawProfile(p);
  return true;
}
/* deleting it: the danger zone (util.js, #287), apart from the name above it; its name typed (no placeholder in the box:
   it would look filled in), with Export everything first */
function profileDelete(p) {
  return dangerZone({title: "Delete this profile", hint: "For everyone it's shared with. It can't be undone.", open: "Delete profile…", name: p.name,
    says: n => el("p", {}, "This deletes ", el("b", {text: n}), " for everyone it's shared with: its records, documents, settings, ",
      "activity log and sharing, and what the watch receiver keeps from its phone. Your original exports stay, and so do backups made before now."),
    first: () => el("a", {class: "btn small", href: `api/profiles/${p.id}/export`, download: "", text: "Export everything first"}),
    action: "Delete it and everything in it",
    run: async typed => {
      const r = await call(`profiles/${p.id}`, "DELETE", {confirm_name: typed});
      try { if (localStorage.getItem("ht-profile") === String(p.id)) localStorage.removeItem("ht-profile"); } catch (e) {}
      toast(`Deleted “${p.name}”.` + (r && r.unlinked_build ? " Its records came from the setup build, which won't be loaded into a profile again unless an admin asks for it." : ""));
      await profileGone();
    }}).node;
}
/* after a delete: the next profile, as if picked (the one deleted isn't asked about: nothing of it can be kept), or
   with none left, your own settings only */
async function profileGone() {
  ME = await call("me");
  PID = null;
  for (const id of ["about", "targets", "profileCard"]) $("#" + id).removeAttribute("data-dirty");
  if (ME.profiles.length) {
    SgNav.profiles(ME.profiles);
    $("#profile").dispatchEvent(new Event("change", {bubbles: true}));
    return goneFocus();
  }
  SgNav.profiles([]);
  for (const id of ["about", "targets", "profileCard"]) $("#" + id).hidden = true;
  $("#noProfiles").hidden = false;
  await load(null, null, true);
  goneFocus();
}
/* focus on the page's heading: the button it was on went with the profile (#417) */
function goneFocus() {
  const h = $("#groupTitle");
  if (!h) return;
  if (!h.hasAttribute("tabindex")) h.tabIndex = -1;
  h.focus();
}
