/* Sanguity · renaming where a name is shown (#38): a lab series (Trends, the heatmap, a result in the drawer), a
   result's source tag (the Trends table) and an import, the source of every record (the drawer's and Trends' Source
   lines). A lab or a tag is one entry of the profile's name maps in the settings (labNicknames, sources: the Names
   section of the Settings page), changed one at a time (PUT .../settings/{key}/name); an import keeps its own name
   (PUT .../imports/{id}/name). The name in the records stays on hover and in the drawer. Owners and editors rename;
   everyone sees the names. Plain script, loaded in order by index.html after sources.js; all files share one global
   scope. */
"use strict";
const canRename = () => !!PROFILE && (PROFILE.role === "owner" || PROFILE.role === "editor");
/* what each kind of name is: the name shown now, the name in the records, and how a new one is saved */
const RENAME = {
  lab: {what: "lab", shown: n => nice(n), original: n => n,
    save: (n, to) => api(`profiles/${PROFILE.id}/settings/labNicknames/name`, {method: "PUT", body: JSON.stringify({name: n, to})}),
    done: j => applySettings(j)},
  tag: {what: "source tag", shown: t => srcName(t), original: t => t,
    save: (t, to) => api(`profiles/${PROFILE.id}/settings/sources/name`, {method: "PUT", body: JSON.stringify({name: t, to})}),
    done: j => applySettings(j)},
  import: {what: "source", shown: i => importName(i), original: i => (sourceOf(i) || {}).label || "",
    save: (i, to) => api(`profiles/${PROFILE.id}/imports/${i}/name`, {method: "PUT", body: JSON.stringify({name: to || null})}),
    done: (j, i) => { const s = sourceOf(i); if (s) { if (j.name) s.name = j.name; else delete s.name; } }},
};
/* "Platelets", with "In the records: Platelet Count" on hover when it's been renamed */
const renamedTitle = (kind, key) => { const r = RENAME[kind], o = r.original(key); return r.shown(key) !== o ? `In the records: ${o}` : o; };

/* the pencil (✎) beside a name (nothing for someone who can't rename) */
function renameButton(kind, key) {
  if (!canRename()) return null;
  const say = `Rename ${RENAME[kind].shown(key)}`;   // the tip says its name, so it isn't its description too (#358)
  const b = el("button", {class: "rename-btn btn quiet small", type: "button", "data-rename": kind, "data-key": String(key), "data-tip": say, "aria-label": say}, icon("pencil"));
  b.addEventListener("click", ev => { ev.stopPropagation(); ev.preventDefault(); openRename(b, kind, key); });
  return b;
}

/* the small form, beside what opened it: the name in the records, the name to show, Save, the original name back.
   A popover="auto" (#363), as a menu is: the browser closes it on Escape (before the drawer's Escape, drawer.js) and on
   a click or tap outside it, and when a menu or another one opens; its own button opens and closes it. As it closes
   with focus in it, or lost (a click on nothing), focus goes back to the button. */
let renameBox = null;
function closeRename(refocus = true) {
  if (!renameBox) return;
  renameBox.refocus = refocus;
  renameBox.box.hidePopover();
}
function openRename(anchor, kind, key) {
  const again = renameBox && renameBox.anchor === anchor;
  closeRename(false); hideTip();
  if (again) return;   // its own button, pressed again: closed
  const r = RENAME[kind], original = r.original(key), shown = r.shown(key);
  const input = el("input", {type: "text", maxlength: kind === "import" ? 80 : 120, "aria-label": `Name shown for ${original}`});
  input.value = shown;
  const msg = el("p", {class: "hint rename-msg", role: "status"});
  const save = async to => {
    sayIn(msg, "Saving…");
    try {
      const res = await r.save(key, to);
      const j = await res.json().catch(() => ({}));
      if (!res.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save the name (${res.status}).`, true); return; }
      r.done(j, key);
      afterRename(kind, key);
    } catch (e) { sayIn(msg, "Couldn't reach the server: " + e.message, true); }
  };
  const box = el("form", {class: "rename-box", role: "dialog", "aria-label": `Rename ${shown}`, popover: "auto"},
    el("label", {}, el("span", {text: `Show “${original}” as`}), input),
    el("div", {class: "rename-acts"},
      el("button", {class: "btn primary", type: "submit", text: "Save"}),
      shown !== original ? el("button", {class: "btn", type: "button", text: "Use the original name", onclick: () => save("")}) : null,
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => closeRename()})),
    el("p", {class: "hint", text: kind === "import" ? "Shown wherever a record says where it came from." : "Shown on every tab, for everyone this profile is shared with."}),
    msg);
  box.addEventListener("submit", ev => { ev.preventDefault(); save(input.value.trim()); });
  const me = {box, anchor, kind, key, refocus: true};
  box.addEventListener("beforetoggle", ev => {
    if (ev.newState !== "closed") return;
    if (renameBox === me) renameBox = null;
    const a = document.activeElement;   // in it, or lost: on the page, or on the drawer itself (a click on its words)
    const lost = box.contains(a) || a === document.body || a.matches("dialog") && a.contains(anchor);
    if (me.refocus && lost && anchor.isConnected) anchor.focus({preventScroll: true});
  });
  box.addEventListener("toggle", ev => { if (ev.newState === "closed") box.remove(); });
  (anchor.closest("dialog") || document.body).append(box);   // in the drawer while it's a modal dialog (#262): the page behind is inert
  renameBox = me;
  // its invoker, a button (not a heatmap's SVG label): a click on it isn't one outside, and its own click handler
  // opens and closes it
  if ("popoverTargetElement" in anchor) anchor.popoverTargetElement = box;
  box.showPopover(anchor instanceof HTMLElement ? {source: anchor} : {});
  placeRename();
  input.focus(); input.select();
}
/* fixed beside the anchor, inside the window (16 px from its edges on a phone) */
function placeRename() {
  if (!renameBox) return;
  const {box, anchor} = renameBox, a = anchor.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight, pad = 8;
  const left = Math.max(pad, Math.min(a.left, innerWidth - w - pad));
  const top = a.bottom + 6 + h <= innerHeight - pad ? a.bottom + 6 : Math.max(pad, a.top - h - 6);
  box.style.left = left + "px"; box.style.top = top + "px";
}
addEventListener("resize", placeRename);
addEventListener("scroll", placeRename, {passive: true, capture: true});   // the page or the drawer scrolled
/* the new name, everywhere: the open tab again, the drawer's event again, then focus back where the rename began */
function afterRename(kind, key) {
  closeRename(false);
  renderAll();
  if (drawer.classList.contains("open") && state.selEv != null && byId.has(state.selEv)) {
    const keep = $("#dbody").scrollTop; openEvent(byId.get(state.selEv)); $("#dbody").scrollTop = keep;
  }
  const again = [...document.querySelectorAll(`[data-rename="${kind}"][data-key="${CSS.escape(String(key))}"]`)]
    .find(b => b.getClientRects().length);   // the one on screen (another tab keeps its own, hidden)
  if (again) again.focus({preventScroll: true});
}
