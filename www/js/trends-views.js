/* Sanguity · saved Trends views (#37): named sets of labs ("CBC", "Liver") kept per profile in the trends.views
   setting (server/settings.py), picked at the top of the Trends lab list. Owners and editors save what's charted as a
   view, make one the default (what Trends charts at first, instead of trends.defaultLabs) and delete one; viewers pick.
   Plain script, loaded after trends.js; all files share one global scope. */
"use strict";
const tvCanSave = () => !!PROFILE && (PROFILE.role === "owner" || PROFILE.role === "editor");
const tvLabs = v => v.labs.filter(n => SERIES.some(s => !s.watch && s.name === n));   // the view's labs this profile has
/* what Trends charts when nothing is chosen on this device: the default view's labs, else trends.defaultLabs */
function startLabs() {
  const v = CFG.trends.views.find(x => x.default), have = v ? tvLabs(v) : [];
  return new Set(have.length ? have : CFG.trends.defaultLabs.filter(n => SERIES.some(s => s.name === n)));
}
/* the saved view that is what's charted now, if one is */
function tvCurrent() {
  const sel = [...state.sel].filter(n => SERIES.some(s => !s.watch && s.name === n));
  return CFG.trends.views.find(v => { const l = tvLabs(v); return l.length === sel.length && l.every(n => state.sel.has(n)); }) || null;
}
let tvMsg = "", tvBad = false, tvFormOpen = false, tvQuiet = false;
/* saved with the views they were made from (was, #923): changed since elsewhere, refused (409), and read again */
async function tvStore(views, said, {quiet = false, was = CFG.trends.views} = {}) {   // quiet: shown beside the views, said by a toast instead (#263)
  let ok = false;
  try {
    const r = await saveWhole(`profiles/${PROFILE.id}/settings/trends.views`, views, was);   // (util.js, #956)
    const j = await r.json().catch(() => ({}));
    if (r.status === 409) { const now = await api(`profiles/${PROFILE.id}/settings`).then(x => x.ok ? x.json() : null).catch(() => null); if (now) applySettings(now); }
    if (!r.ok) { tvMsg = typeof j.detail === "string" ? j.detail : `Couldn't save the views (${r.status}).`; tvBad = true; renderSeriesList(); return false; }
    applySettings(j);
    tvMsg = said; tvQuiet = quiet; ok = true;
  } catch (e) { tvMsg = "Couldn't reach the server: " + e.message; tvBad = true; }
  tvFormOpen = false;
  renderSeriesList();
  return ok;
}
/* after a lab is ticked or unticked in the list: the bar again (the current view, Save's count), keeping a typed name */
function tvRefresh() {
  const box = $("#tvBox"); if (!box) return;
  const typed = $("#viewName")?.value, focus = document.activeElement?.id;
  box.replaceChildren(...[viewsBar()].filter(Boolean));
  if (typed != null && $("#viewName")) $("#viewName").value = typed;
  if (focus && box.querySelector("#" + focus)) $("#" + focus).focus();
}
const tvPlain = v => ({name: v.name, labs: v.labs, default: !!v.default});
/* a deleted view's Undo (#359): the view put back into the views as they are now on the server, not the list as it
   was when it was deleted (other views deleted, saved or made the default since stay as they are): back in its place
   (before the view that followed it, or after the one before it, of those still there), the default again only if
   no other view is now; a view saved under its name since is left as it is. order: the views' names it was among. */
async function tvPutBack(gone, order) {
  const r = await api(`profiles/${PROFILE.id}/settings`);
  if (!r.ok) throw new Error(`the server answered ${r.status}`);
  const j = await r.json(), now = (j.trends?.views || []).map(tvPlain);
  if (now.some(v => v.name.toLowerCase() === gone.name.toLowerCase())) {
    applySettings(j);   // drawn as they are
    tvMsg = `A view called “${gone.name}” was saved since: it's left as it is.`;
    renderSeriesList();
    return;
  }
  const i = order.indexOf(gone.name), at = n => now.findIndex(v => v.name === n);
  const after = order.slice(i + 1).map(at).find(k => k >= 0), before = order.slice(0, i).reverse().map(at).find(k => k >= 0);
  const next = [...now];
  next.splice(after ?? (before == null ? next.length : before + 1), 0, {...gone, default: gone.default && !now.some(v => v.default)});
  if (!await tvStore(next, `Undone: “${gone.name}” is back.`, {was: j.trends?.views})) throw new Error("it couldn't be saved");
}
/* the bar at the top of the lab list: pick a view; save, make default, delete */
function viewsBar() {
  const views = CFG.trends.views, can = tvCanSave(), cur = tvCurrent();
  if (!views.length && !can) return null;
  const msg = el("p", {class: "hint tv-msg"}); sayIn(msg, tvMsg, tvBad, {say: !tvQuiet}); tvMsg = ""; tvBad = tvQuiet = false;   // shown and said (#209)
  const pick = el("select", {id: "viewPick", "aria-label": "Saved view", disabled: views.length ? null : "",
    onchange: () => { const v = views.find(x => x.name === pick.value); if (!v) return;
      const have = tvLabs(v); state.sel = new Set(have);
      tvMsg = have.length < v.labs.length ? `${v.labs.length - have.length} of its labs have no results here.` : "";
      renderLabs(); $("#viewPick")?.focus(); }},
    el("option", {value: "", text: views.length ? (cur ? "" : "Saved views…") : "No saved views yet"}),
    ...views.map(v => el("option", {value: v.name, text: v.name + (v.default ? " (default)" : "")})));
  if (cur) { pick.firstChild.remove(); pick.value = cur.name; }
  const bar = el("div", {class: "tv-bar"}, pick);
  if (can && cur) {
    bar.append(
      el("button", {class: "btn", type: "button", id: "viewDefault", "aria-pressed": cur.default ? "true" : "false",
        "data-tip": cur.default ? "Labs starts with this view. Press to stop." : "Start Labs with this view",
        onclick: async () => { await tvStore(views.map(v => ({...tvPlain(v), default: v === cur ? !cur.default : false})),
          cur.default ? `“${cur.name}” is no longer the default.` : `Labs now starts with “${cur.name}”.`);
          $("#viewDefault")?.focus(); }}, ...(cur.default ? ["Default ", icon("check")] : ["Make default"])),   // drawn again: focus on it again (#376)
      el("button", {class: "btn", type: "button", text: "Delete", "aria-label": `Delete the view ${cur.name}`,
        onclick: async () => {   // at once, with an Undo that puts this view back (#263; it asked first before)
          const gone = tvPlain(cur), order = views.map(v => v.name), said = `Deleted “${cur.name}”.`;
          if (await tvStore(views.filter(v => v !== cur).map(tvPlain), said, {quiet: true})) {
            // the Delete button is drawn away: focus on what stays, to come back to from the toast (#361)
            ($("#viewPick:not(:disabled)") || $("#viewSaveOpen:not(:disabled)") || $("#tab-labs"))?.focus();
            toast(said, {action: "Undo", onAction: () => tvPutBack(gone, order)});
          } }}));
  }
  if (can && !tvFormOpen) bar.append(el("button", {class: "btn", type: "button", id: "viewSaveOpen", text: "Save view…", disabled: state.sel.size ? null : "",
    "data-tip": state.sel.size ? "Save the labs charted now as a view" : "Pick some labs first",
    onclick: () => { tvFormOpen = true; renderSeriesList(); $("#viewName")?.focus(); }}));
  const out = el("div", {class: "tv"}, bar);
  if (can && tvFormOpen) {
    const name = el("input", {id: "viewName", maxlength: 60, placeholder: "Name, like CBC", "aria-label": "Name of the view", value: cur ? cur.name : ""});
    const save = () => {
      const n = name.value.trim().replace(/\s+/g, " ");
      if (!n) { sayIn(msg, "Give the view a name.", true); name.focus(); return; }
      const labs = [...state.sel].filter(x => SERIES.some(s => !s.watch && s.name === x));
      if (!labs.length) { sayIn(msg, "Pick some labs first.", true); return; }
      const old = views.find(v => v.name.toLowerCase() === n.toLowerCase());
      const next = old ? views.map(v => v === old ? {...tvPlain(v), name: n, labs} : tvPlain(v)) : [...views.map(tvPlain), {name: n, labs, default: false}];
      tvStore(next, old ? `Updated “${n}” (${labs.length} lab${labs.length === 1 ? "" : "s"}).` : `Saved “${n}” (${labs.length} lab${labs.length === 1 ? "" : "s"}).`);
    };
    name.addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); save(); } if (ev.key === "Escape") { tvFormOpen = false; renderSeriesList(); $("#viewSaveOpen")?.focus(); } });
    out.append(el("div", {class: "tv-form"}, name,
      el("button", {class: "btn primary", type: "button", id: "viewSave", text: "Save", onclick: save}),
      el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => { tvFormOpen = false; renderSeriesList(); $("#viewSaveOpen")?.focus(); }})),
      el("p", {class: "hint tv-note", text: `${state.sel.size} lab${state.sel.size === 1 ? "" : "s"} charted now. A view with the same name is replaced.`}));
  }
  out.append(msg);
  return out;
}
