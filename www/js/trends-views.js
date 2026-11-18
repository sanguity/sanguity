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
let tvMsg = "", tvBad = false, tvFormOpen = false;
async function tvStore(views, said) {
  try {
    const r = await api(`profiles/${PROFILE.id}/settings/trends.views`, {method: "PUT", body: JSON.stringify({value: views})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { tvMsg = typeof j.detail === "string" ? j.detail : `Couldn't save the views (${r.status}).`; tvBad = true; renderSeriesList(); return false; }
    applySettings(j);
    tvMsg = said;
  } catch (e) { tvMsg = "Couldn't reach the server: " + e.message; tvBad = true; }
  tvFormOpen = false;
  renderSeriesList();
  return true;
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
/* the bar at the top of the lab list: pick a view; save, make default, delete */
function viewsBar() {
  const views = CFG.trends.views, can = tvCanSave(), cur = tvCurrent();
  if (!views.length && !can) return null;
  const msg = el("p", {class: "hint tv-msg"}); sayIn(msg, tvMsg, tvBad); tvMsg = ""; tvBad = false;   // shown and said (#209)
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
      el("button", {class: "btn", type: "button", "aria-pressed": cur.default ? "true" : "false",
        title: cur.default ? "Labs starts with this view. Press to stop." : "Start Labs with this view",
        onclick: () => tvStore(views.map(v => ({...tvPlain(v), default: v === cur ? !cur.default : false})),
          cur.default ? `“${cur.name}” is no longer the default.` : `Labs now starts with “${cur.name}”.`)}, ...(cur.default ? ["Default ", icon("check")] : ["Make default"])),
      el("button", {class: "btn", type: "button", text: "Delete", "aria-label": `Delete the view ${cur.name}`,
        onclick: async () => {
          if (!await ask({title: `Delete the view “${cur.name}”?`, message: "The labs it shows stay charted; only the saved view goes.",
                          action: "Delete view", danger: true})) return;
          tvStore(views.filter(v => v !== cur).map(tvPlain), `Deleted “${cur.name}”.`); }}));
  }
  if (can && !tvFormOpen) bar.append(el("button", {class: "btn", type: "button", id: "viewSaveOpen", text: "Save view…", disabled: state.sel.size ? null : "",
    title: state.sel.size ? "Save the labs charted now as a view" : "Pick some labs first",
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
