/* Sanguity · Settings → Dashboard → "Start from a condition" (#186): pick one or more conditions (searchable, grouped
   by body system, so a list of a hundred stays usable), see what they'd fill in, then fill it in. The definitions are
   condition-presets.json's (server/presets.py), each lab already matched to this profile's own series by the server.

   Filling in only changes the editors below (and, if ticked, Saved views and the visit summary's ticks, each in its
   own section): the person saves them as any other change, or Discard changes puts them back; Reset to defaults
   after saving. Applied once, not linked: nothing here is kept, and a later change to a preset changes no one's
   settings. Not medical advice: "commonly followed for", as its guideline says; labs with no results here are named
   without suggesting anyone get them. Uses settings.js's call, PID, LABS, nick and SET_ITEMS; nothing goes into
   innerHTML. */
"use strict";
const PRESET_TILES = 12;   // a preset's tiles at most (the default list's length)
let PRESETS = null;        // {pid, data}: the server's offer for this profile, fetched once per profile

const presetText = list => list.length < 2 ? list.join("") : list.slice(0, -1).join(", ") + " and " + list.at(-1);

/* the conditions' labs, interleaved so each one's most important come first, without repeats: {have: labs with a
   series here (the tiles), missing: labs this profile has no results for} */
function presetLabs(conds) {
  const have = [], missing = [], seen = new Set(), series = new Set();
  const most = Math.max(0, ...conds.map(c => c.labs.length));
  for (let r = 0; r < most; r++) for (const c of conds) {
    const x = c.labs[r];
    if (!x || seen.has(x.code) || (x.series && series.has(x.series))) continue;
    seen.add(x.code);
    if (x.series) { series.add(x.series); have.push(x); } else missing.push(x);
  }
  return {have, missing};
}

/* what filling in would set, from the editors' values now: {keyLabs, homeTiles, ticked, views} (ticked and views
   only when asked for) */
function presetPlan(conds, replace, withViews, withSummary) {
  const cur = k => SET_ITEMS[k] ? SET_ITEMS[k].value() : [];
  const {have} = presetLabs(conds);
  const mine = have.slice(0, PRESET_TILES).map(x => x.series);
  const keyLabs = replace ? mine : [...cur("overview.keyLabs"), ...mine.filter(n => !cur("overview.keyLabs").includes(n))].slice(0, 50);
  const home = conds.flatMap(c => c.home);
  const tiles = [...new Set(home.map(h => h.tile).filter(Boolean))], secs = [...new Set(home.map(h => h.section))];
  const plan = {keyLabs, homeTiles: replace ? tiles : [...new Set([...cur("overview.homeTiles"), ...tiles])]};
  if (withSummary && SET_ITEMS["summary.ticked"]) plan.ticked = replace ? secs : [...new Set([...cur("summary.ticked"), ...secs])];
  if (withViews && SET_ITEMS["trends.views"]) {
    const views = [...cur("trends.views")];
    for (const c of conds) {
      const labs = presetLabs([c]).have.map(x => x.series);
      if (!labs.length) continue;
      const v = {name: c.name.slice(0, 60), labs, default: false}, i = views.findIndex(w => w.name.toLowerCase() === v.name.toLowerCase());
      if (i >= 0) views[i] = {...v, default: views[i].default}; else views.push(v);
    }
    plan.views = views.slice(0, 30);
  }
  return plan;
}

function presetsBlock(sec) {
  const st = sec.settings.find(s => s.key === "overview.keyLabs");
  if (PID == null || !st || !st.can_change || !PRESETS || PRESETS.pid !== PID) return null;   // viewers see the tiles as they are
  const picked = [];
  const msg = el("span", {class: "msg"}), count = el("span", {class: "hint pre-count", id: "preCount"});
  const sugg = el("div", {class: "pre-suggest"}), chosen = el("p", {class: "pre-chosen", id: "preChosen"});
  const list = el("div", {class: "pre-list", id: "preList"}), out = el("div", {class: "pre-out", id: "preOut", hidden: ""});
  const find = el("input", {type: "search", id: "preFind", autocomplete: "off", placeholder: "Diabetes, kidney, CKD…",
    "aria-describedby": "preCount", oninput: () => filter()});
  const panel = el("div", {class: "pre-panel", id: "prePanel", hidden: ""},
    el("div", {class: "pre-find"}, el("label", {for: "preFind", text: "Find a condition"}), find, count),
    chosen, list, out);
  const open = el("button", {class: "btn small", type: "button", id: "preOpen", text: "Choose conditions…", "aria-expanded": false,
    "aria-controls": "prePanel", onclick: () => show(panel.hidden)});
  const node = el("div", {class: "set set-presets", id: "presets"},
    el("h3", {class: "sub", text: "Start from a condition"}),
    el("p", {class: "hint", text: "Fill in the tiles with the labs and home readings commonly followed for one or more " +
      "conditions, as a published guideline lists them. It only chooses what's shown: it doesn't judge results or suggest " +
      "tests. It's filled in once, below, for you to change and save as usual."}),
    sugg, open, panel);
  const show = on => {
    panel.hidden = !on; open.setAttribute("aria-expanded", String(on));
    open.textContent = on ? "Close the conditions" : "Choose conditions…";
    if (on) find.focus();
  };

  let data = null;
  const byId = id => data.conditions.find(c => c.id === id);
  function pick(id, on) {
    const i = picked.indexOf(id);
    if (on && i < 0) picked.push(id); else if (!on && i >= 0) picked.splice(i, 1);
    const box = list.querySelector(`input[value="${id}"]`);
    if (box) box.checked = on;
    drawChosen(); drawOut();
  }
  function drawList() {
    list.replaceChildren(...data.systems.map(sys => {
      const cs = data.conditions.filter(c => c.system === sys.id).sort((a, b) => a.name.localeCompare(b.name));
      return cs.length ? el("fieldset", {class: "pre-sys", "data-system": sys.id}, el("legend", {text: sys.name}),
        ...cs.map(c => el("label", {class: "check", "data-id": c.id},
          el("input", {type: "checkbox", value: c.id, checked: picked.includes(c.id) || null, onchange: ev => pick(c.id, ev.target.checked)}),
          el("span", {text: c.name}), el("span", {class: "muted pre-also"})))) : null;
    }).filter(Boolean));
    filter(true);
  }
  function filter(quiet) {
    const q = find.value.trim().toLowerCase();
    let n = 0;
    for (const lab of list.querySelectorAll("label[data-id]")) {
      const c = byId(lab.dataset.id), syn = q && !c.name.toLowerCase().includes(q) ? c.synonyms.find(s => s.toLowerCase().includes(q)) : null;
      const hit = !q || c.name.toLowerCase().includes(q) || !!syn;
      lab.hidden = !hit; n += hit;
      lab.querySelector(".pre-also").textContent = syn ? ` · also called ${syn}` : "";
    }
    for (const fs of list.querySelectorAll("fieldset")) fs.hidden = ![...fs.querySelectorAll("label[data-id]")].some(l => !l.hidden);
    const total = data.conditions.length;
    const text = !q ? `${total} condition${total === 1 ? "" : "s"}` : n ? `${n} of ${total} match` : `None of the ${total} match “${find.value.trim()}”`;
    if (quiet) count.textContent = text; else sayIn(count, text, false, {repeat: false});   // said only as you type
  }
  function drawChosen() {
    chosen.replaceChildren(...(picked.length ? [el("span", {class: "muted", text: "Chosen: "}),
      ...picked.map(id => el("button", {class: "chip", type: "button", text: byId(id).name + " ×", "aria-label": `Remove ${byId(id).name}`,
        onclick: () => { pick(id, false); (chosen.querySelector("button") || find).focus(); }}))]
      : [el("span", {class: "muted", text: "None chosen yet: tick one or more below."})]));
  }

  /* the preview: where each condition's list comes from, the tiles as the Overview would show them, the labs with no
     results here, what's left out for a child, the home readings; then how to fill it in */
  const mode = el("fieldset", {class: "set-radios pre-mode"}, el("legend", {class: "set-sr", text: "Add or replace"}),
    radioSeg([["add", "Add to my tiles"], ["replace", "Replace my tiles"]].map(([v, l]) => el("label", {},
      el("input", {type: "radio", name: "preMode", value: v, checked: v === "add" || null, onchange: () => drawOut()}), l))));
  const viewsBox = el("input", {type: "checkbox", id: "preViews", onchange: () => drawOut()});
  const sumBox = el("input", {type: "checkbox", id: "preSummary", onchange: () => drawOut()});
  const fill = el("button", {class: "btn primary small", type: "button", id: "preFill", text: "Fill these in", onclick: () => apply()});
  const replace = () => mode.querySelector("input:checked")?.value === "replace";
  function drawOut() {
    const conds = picked.map(byId);
    out.hidden = !conds.length;
    if (!conds.length) { sayIn(msg, ""); return; }
    const plan = presetPlan(conds, replace(), viewsBox.checked, sumBox.checked);
    const {missing} = presetLabs(conds), before = SET_ITEMS["overview.keyLabs"].value();
    const tiles = keyLabTiles(plan.keyLabs, LABS, false).map(n => {
      const s = LABS.find(x => x.name === n), last = s.pts[s.pts.length - 1];
      return el("div", {class: "tile", "data-lab": n},
        el("span", {class: "lbl"}, el("span", {text: nick(n), title: n}), before.includes(n) ? null : el("span", {class: "tag pre-new", text: "new"})),
        el("span", {class: "val"}, last[5] || String(last[1]), el("small", {text: s.unit || ""})));
    });
    const home = [...new Set(conds.flatMap(c => c.home.map(h => h.label)))];
    const left = [...new Set(conds.flatMap(c => c.adultOnly))];
    const tick = SET_ITEMS["summary.ticked"], secs = [...new Set(conds.flatMap(c => c.home.map(h => (tick?.st.choices || {})[h.section] || h.label)))];
    out.replaceChildren(...[
      el("h4", {class: "set-ph", text: "Where each list comes from"}),
      el("ul", {class: "pre-src"}, ...conds.map(c => el("li", {},
        el("strong", {text: c.name}), ": commonly followed for it according to ",
        ...c.sources.flatMap((s, i) => [i ? (i === c.sources.length - 1 ? " and " : ", ") : "",
          `${s.publisher}, `, el("a", {href: s.url, target: "_blank", rel: "noopener noreferrer", text: s.title}), ` (${s.year})`]),
        ". ", el("span", {class: "muted", text: c.reviewed ? "Reviewed by a clinician." : "Not reviewed by a clinician yet."})))),
      mode,
      el("h4", {class: "set-ph", text: replace() ? "Key lab tiles, instead of yours" : "Key lab tiles, after yours"}),
      el("div", {class: "tiles pre-tiles", role: "group", "aria-label": "Preview of the key lab tiles"},
        ...(tiles.length ? tiles : [el("p", {class: "hint", text: "None of these labs has results in this profile yet, so they'd add no tiles."})])),
      missing.length ? el("p", {class: "hint", id: "preMissing", text: `No results in this profile's records, so no tile: ` +
        missing.map(x => nick(x.name)).join("; ") + "."}) : null,   // (semicolons: some names have commas)
      left.length ? el("p", {class: "hint", id: "preChild", text: `Left out for a child's profile (About this person's birth date): ` +
        `${presetText(left)}, ${left.length === 1 ? "a measure" : "measures"} for adults.`}) : null,
      el("p", {class: "hint", id: "preHome"}, home.length ? `Home readings: ${presetText(home)}. ` +
        (plan.homeTiles.length ? `Overview tiles: ${presetText(plan.homeTiles.map(t => HOME_TILE_NAMES[t] || t))}.` : "") : "No home readings for these."),
      el("label", {class: "check"}, viewsBox, `Also save a view on Labs for each condition, with its labs that have results`),
      secs.length && tick ? el("label", {class: "check"}, sumBox, `Also tick ${presetText(secs)} in the visit summary at first`) : null,
      el("div", {class: "set-add"}, fill, msg)].filter(Boolean));
  }
  const HOME_TILE_NAMES = {bp: "Blood pressure", glucose: "Blood glucose", temp: "Body temperature"};

  function apply() {
    const conds = picked.map(byId);
    if (!conds.length) return;
    const plan = presetPlan(conds, replace(), viewsBox.checked, sumBox.checked);
    const keys = {keyLabs: "overview.keyLabs", homeTiles: "overview.homeTiles", ticked: "summary.ticked", views: "trends.views"};
    const where = [];
    for (const [k, key] of Object.entries(keys)) {
      const it = SET_ITEMS[key];
      if (!(k in plan) || !it || same(it.value(), plan[k])) continue;
      it.put(plan[k]);
      where.push(k === "views" ? "Saved views (Lab charts)" : k === "ticked" ? "the visit summary's ticks" : it.st.label);
    }
    sayIn(msg, where.length ? `Filled in: ${presetText(where)}. Save each section to keep ${where.length === 1 ? "it" : "them"}, or Discard changes.`
      : "Nothing to change: these are filled in already.");
    drawOut();
    fill.focus();
  }

  data = PRESETS.data;
  {
    if (data.child) panel.prepend(el("p", {class: "hint", id: "preKid", text: "This is a child's profile (About this person), so labs meant for adults are left out."}));
    sugg.replaceChildren(...data.suggest.map(s => el("p", {class: "pre-offer"},
      `The problem list in these records has “${s.problem}”. Start from the preset for ${byId(s.id).name}? `,
      el("button", {class: "btn small", type: "button", text: `Choose ${byId(s.id).name}`, onclick: () => {
        show(true); pick(s.id, true); list.querySelector(`input[value="${s.id}"]`)?.focus(); }}))));
    drawList(); drawChosen();
  }
  return node;
}

/* the offer for this profile, fetched with the sections (settings.js load) for those who may change its settings, so
   the block is drawn whole at once and nothing below it moves when it arrives */
async function presetsLoad(pid, role) {
  if (pid == null || !["owner", "editor"].includes(role) || (PRESETS && PRESETS.pid === pid)) return;
  try { PRESETS = {pid, data: await call(`profiles/${pid}/condition-presets`)}; } catch (e) { PRESETS = null; }
}
