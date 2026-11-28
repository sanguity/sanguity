/* Sanguity · lab name matches (#1097): a test a lab names differently charted with the test the profile has already
   ("Plt Count" with "Platelet Count"). Each profile's own, kept by server/labmap.py (routes_labmaps.py): made at an
   import's review, here, or from a lab's panel on Labs (Same test as…), the same saved match wherever it's made, used
   for later imports, pulls and results typed in. A change can move the results already saved too (re-filed: the
   records keep the name their import gave them; the page charts them with the test), and every change has an Undo.
   Lab names (Labs and ranges → Names, settings.js) is another thing: it changes only how a name is shown.

   lmPick(o): the one picker, a question (util.js ask) with the profile's tests (unit beside each) and Keep as its
   own test. drawLabMatches(p): Settings → This person → Labs and ranges, the card #labmatches, with the matches an
   admin made for every profile before #927 under their own heading (#1114: the one place; Install had a page of
   them), changed there by admins, read by everyone else. lmSameAs(s): Labs' lab
   panel's Same test as… (lab-panel.js). Plain script; all files share one scope. */
"use strict";
const LM_KEEP = "";   // the picker's Keep as its own test: no test has an empty name
const lmUnit = u => u ? ` (${unitSaid(u)})` : "";   // as people write it (util.js, #1103)
const lmPlural = (n, w) => `${n.toLocaleString("en-US")} ${w}${n === 1 ? "" : "s"}`;
async function lmCall(path, method = "GET", body) {
  const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(typeof j.detail === "string" ? j.detail : `${r.status} ${r.statusText}`), {status: r.status});
  return j;
}
/* the picker: o.title; o.name, the test as charted now (null: picked here too, Add); o.tests [{name, unit}] the
   profile's; o.current, the test it goes to now (null kept as its own, undefined none yet); o.results, how many saved
   results it covers (null: not known); o.shown(name), a name as the page shows it (Labs': its nickname). -> {name, target (null: kept as its own), move} or null if cancelled */
async function lmPick({title, name = null, tests, current, results = null, action = "Save", shown = n => n}) {
  const id = "lmPick" + (lmPick.n = (lmPick.n || 0) + 1);
  const opt = (t, sel) => el("option", {value: t.name, text: shown(t.name) + lmUnit(t.unit), selected: sel || null});
  const from = name == null ? el("select", {id: id + "f"}, tests.map(t => opt(t, false))) : null;
  const to = el("select", {id: id + "t"});
  const fill = () => {   // every test but the one it's for, then Keep as its own test
    const of = from ? from.value : name, was = to.value || (current === undefined ? null : current ?? LM_KEEP);
    to.replaceChildren(...tests.filter(t => t.name !== of).map(t => opt(t, t.name === was)),
      el("option", {value: LM_KEEP, text: "Keep as its own test", selected: was === LM_KEEP || null}));
  };
  fill();
  if (from) from.onchange = fill;
  // the results already saved: moved too (re-filed), or left where they're charted; a list, as the others (no CSS of its own)
  const move = el("select", {id: id + "m"}, el("option", {value: "1", text: "Move them too"}), el("option", {value: "", text: "Leave them where they're charted"}));
  const saved = results == null ? "Its results already saved" : `Its ${lmPlural(results, "result")} already saved`;
  const yes = await ask({title, action, field: from || to, message: [
    from ? el("label", {for: from.id, text: "Lab name, as charted now"}) : null, from,
    el("label", {for: to.id, text: name == null ? "Same test as" : `Chart ${shown(name)} with`}), to,
    results === 0 ? null : el("label", {for: move.id, text: saved}), results === 0 ? null : move,
    el("p", {class: "hint", text: "Later imports, pulls and results typed in follow it either way. Each result keeps the name its lab gave it; you can undo this."})]});
  if (!yes) return null;
  return {name: from ? from.value : name, target: to.value === LM_KEEP ? null : to.value, move: results !== 0 && !!move.value};
}
/* what a change did, in words: "“Plt Count” now goes to Platelet Count; its 14 saved results moved." */
function lmSaid(name, target, moved, move) {
  const goes = target == null ? `“${name}” is kept as its own test` : `“${name}” is now charted with ${target}`;
  return `${goes}${move ? `; ${lmPlural(moved, "saved result")} moved` : "; results already saved stay where they are"}.`;
}

/* ---- Settings → This person → Labs and ranges (settings.html #labmatches) */
let LM_P = null, LM_FIND = "", LM_AT = 0;   // the profile drawn; what's typed in Find; where the row acted on was
async function drawLabMatches(p, focus) {
  LM_P = p;
  const card = $("#labmatches"), body = $("#labmatchesBody"), msg = $("#labmatchesMsg");
  card.hidden = false;
  $("#lmImport").href = `import.html?profile=${p.id}`;
  let d;
  try { d = await lmCall(`profiles/${p.id}/labmaps`); }
  catch (e) { body.replaceChildren(); sayIn(msg, e.message, true); return; }
  if (LM_P !== p) return;
  const edit = d.can_change, list = d.matches, every = d.every || [];
  const again = f => drawLabMatches(p, f);
  // an Undo for a change just made: put back as it was, drawn again (409: changed since, said); all: one for every profile's
  const undoable = (text, change, f, all) => toast(text, {action: "Undo", onAction: async () => {
    try { await lmCall(all ? `labmaps/undo/${change}?profile=${p.id}` : `profiles/${p.id}/labmaps/undo/${change}`, "POST"); sayIn(msg, "Undone: back as it was."); }
    catch (e) { sayIn(msg, e.message, true); }
    await again(f);
  }});
  const arrow = m => [el("span", {"aria-hidden": "true", text: " → "}), el("span", {class: "vh", text: ", "}),
    m.target == null ? "kept as its own test" : el("span", {}, el("span", {class: "vh", text: "charted with "}), m.target)];
  const row = (m, i) => {
    const hd = "lm" + m.id;
    const who = [`From ${m.label}, ${fmtD(m.created * 1000)}${m.created_by ? `, by ${m.created_by}` : ""}`,
      m.changed ? `changed ${fmtD(m.changed * 1000)}${m.changed_by ? ` by ${m.changed_by}` : ""}` : null,
      `${lmPlural(m.imports, "import")} · ${lmPlural(m.results, "result")}`].filter(Boolean).join(" · ");
    const btn = (text, label, run) => el("button", {class: "btn small", type: "button", text, "aria-label": label, onclick: run});
    const acts = !edit ? null : m.check ? [
      btn("Keep", `Keep the match for ${m.name}`, async () => {
        try { const r = await lmCall(`profiles/${p.id}/labmaps/${m.id}`, "PATCH", {target: m.target, keep: true});
          sayIn(msg, `Kept: “${m.name}” is charted with ${m.target ?? "its own test"} from now on.`); undoable("Kept the match.", r.change, m.id); }
        catch (e) { sayIn(msg, e.message, true); }
        again(m.id); }),
      btn("Forget", `Forget the match for ${m.name}`, () => remove(m, "Forgot"))] : [
      btn("Change…", `Change the match for ${m.name}`, async () => {
        const got = await lmPick({title: `Change the match for ${m.name}`, name: m.name, tests: d.tests, current: m.target, results: m.results});
        if (!got) return;
        try { const r = await lmCall(`profiles/${p.id}/labmaps/${m.id}`, "PATCH", {target: got.target, move: got.move});
          const t = lmSaid(m.name, got.target, r.results, got.move); sayIn(msg, t); undoable(t, r.change, m.id); }
        catch (e) { sayIn(msg, e.message, true); }
        again(m.id); }),
      btn("Remove", `Remove the match for ${m.name}`, () => remove(m, "Removed"))];
    return setRow([el("h3", {class: "sub", id: hd}, m.name, lmUnit(m.unit), ...arrow(m)), el("p", {class: "muted", text: who})],
      acts ? el("div", {class: "acts", onclick: () => { LM_AT = i; }}, ...acts) : null, {tag: "section", "aria-labelledby": hd, "data-labmatch": m.id});
  };
  // one for every profile (#1114): no results counted (whose would they be); an admin's Keep, Change… and Remove,
  // Install's routes told the profile, for its Undo; the picker's tests the profile's, with the one it goes to now
  const allRow = (m, i) => {
    const hd = "lm" + m.id, at = `labmaps/${m.id}?profile=${p.id}`, f = "e" + m.id;
    const who = [`From ${m.label}, ${fmtD(m.created * 1000)}${m.created_by ? `, by ${m.created_by}` : ""}`,
      m.changed ? `changed ${fmtD(m.changed * 1000)}${m.changed_by ? ` by ${m.changed_by}` : ""}` : null,
      m.check ? "to check: not used until an admin keeps it" : null].filter(Boolean).join(" · ");
    const btn = (text, label, run) => el("button", {class: "btn small", type: "button", text, "aria-label": `${label} for every profile`, onclick: run});
    const drop = async verb => {
      try { const r = await lmCall(at, "DELETE");
        const t = `${verb} the match for “${m.name}” for every profile: the next import with that name asks again.`; sayIn(msg, t); undoable(t, r.change, f, true); }
      catch (e) { sayIn(msg, e.message, true); }
      again(f);
    };
    const acts = !d.can_change_every ? null : m.check ? [
      btn("Keep", `Keep the match for ${m.name}`, async () => {
        try { const r = await lmCall(at, "PATCH", {target: m.target, keep: true});
          sayIn(msg, `Kept: “${m.name}” is charted with ${m.target ?? "its own test"} for every profile from now on.`); undoable("Kept the match.", r.change, f, true); }
        catch (e) { sayIn(msg, e.message, true); }
        again(f); }),
      btn("Forget", `Forget the match for ${m.name}`, () => drop("Forgot"))] : [
      btn("Change…", `Change the match for ${m.name}`, async () => {
        const tests = m.target == null || d.tests.some(t => t.name === m.target) ? d.tests : [...d.tests, {name: m.target, unit: ""}];
        const got = await lmPick({title: `Change the match for ${m.name}, for every profile`, name: m.name, tests, current: m.target, results: 0});
        if (!got) return;
        try { const r = await lmCall(at, "PATCH", {target: got.target});
          const t = lmSaid(m.name, got.target, 0, false).replace(";", " for every profile;"); sayIn(msg, t); undoable(t, r.change, f, true); }
        catch (e) { sayIn(msg, e.message, true); }
        again(f); }),
      btn("Remove", `Remove the match for ${m.name}`, () => drop("Removed"))];
    return setRow([el("h3", {class: "sub", id: hd}, m.name, lmUnit(m.unit), ...arrow(m)), el("p", {class: "muted", text: who})],
      acts ? el("div", {class: "acts", onclick: () => { LM_AT = list.length + i; }}, ...acts) : null, {tag: "section", "aria-labelledby": hd, "data-every": m.id});
  };
  const all = every.length ? el("div", {id: "lmEvery"}, el("h3", {class: "sub", id: "lmEveryHd", text: "For every profile on this server"}),
    el("p", {class: "hint", text: "Made by an admin before each profile had its own; this profile's own is used before them. " +
      (d.can_change_every ? "A change here is for every profile." : "An admin changes them.")}),
    ...every.map(allRow)) : null;
  // removed at once, no question: an Undo puts it back as it was
  async function remove(m, verb) {
    try {
      const r = await lmCall(`profiles/${p.id}/labmaps/${m.id}`, "DELETE");
      const t = `${verb} the match for “${m.name}”: its saved results stay ${m.target == null ? "as its own test" : `with ${m.target}`}, and the next import with that name asks again.`;
      sayIn(msg, t); undoable(t, r.change, m.id);
    } catch (e) { sayIn(msg, e.message, true); }
    again(m.id);
  }
  const add = edit && d.tests.length > 1 ? el("button", {class: "btn small", type: "button", id: "lmAdd", text: "Add a match…", onclick: async () => {
    const got = await lmPick({title: "Add a match", tests: d.tests});
    if (!got) return;
    try { const r = await lmCall(`profiles/${p.id}/labmaps`, "POST", {name: got.name, target: got.target, move: got.move});
      const t = lmSaid(got.name, got.target, r.results, got.move); sayIn(msg, t); undoable(t, r.change, "lmAdd"); }
    catch (e) { sayIn(msg, e.message, true); }
    again("lmAdd"); }}) : null;
  const check = list.filter(m => m.check), rest = list.filter(m => !m.check);
  const find = list.length > 10 ? el("input", {type: "search", id: "lmFind", value: LM_FIND, autocomplete: "off", spellcheck: "false"}) : null;
  const rows = el("div", {}, ...check.length ? [el("h3", {class: "sub", text: `${check.length} to check`}),
      el("p", {class: "hint", text: "Remembered before Sanguity matched names by its rule of today, so not used until kept: Keep uses it from now on, Forget lets the next import ask."}),
      ...check.map(row), rest.length ? el("h3", {class: "sub", text: "In use"}) : null] : [], ...rest.map((m, i) => row(m, check.length + i)));
  const none = el("p", {class: "hint", role: "status"});
  const filter = () => {
    LM_FIND = find.value;
    const q = find.value.trim().toLowerCase(); let n = 0;
    for (const r of rows.querySelectorAll("[data-labmatch]")) { const show = !q || r.textContent.toLowerCase().includes(q); r.hidden = !show; n += show; }
    none.textContent = q && !n ? "No match has that in it." : "";
  };
  if (find) find.oninput = filter;
  body.replaceChildren(
    add || find ? el("div", {class: "row"}, add, find ? el("label", {}, "Find a match ", find) : null) : null,
    list.length ? rows : emptyState("None yet. Matches are made when you review an import's lab names" + (add ? ", or here with Add a match." : "."),
      edit ? {href: `import.html?profile=${p.id}`, text: "Import results"} : null, {cls: ""}),
    none, all);
  if (find && LM_FIND) filter();
  if (focus === "lmAdd" && $("#lmAdd")) $("#lmAdd").focus();
  else if (focus != null) {   // back where it was (#493's way): the row's first button, else the one now in its place, else Add or the heading
    // (#1114) the ones for every profile after the profile's own, each by "e" and its id
    const shown = [...body.querySelectorAll("[data-labmatch], [data-every]")];
    const at = shown.findIndex(r => (r.dataset.labmatch ?? "e" + r.dataset.every) === String(focus));
    focusNear(shown, at < 0 ? Math.min(LM_AT, shown.length) : at, "button", $("#lmAdd") || $("#labmatchesHd"));
  }
}

/* ---- Labs: a lab's panel's Same test as… (lab-panel.js), the same picker, the records put in place after */
async function lmSameAs(s) {
  const seen = new Set(), tests = [];
  for (const x of SERIES) if (!x.watch && x.name !== s.name && !seen.has(x.name)) { seen.add(x.name); tests.push({name: x.name, unit: x.unit}); }
  tests.sort((a, b) => a.name.localeCompare(b.name));
  const n = SERIES.filter(x => x.name === s.name).reduce((k, x) => k + x.pts.length, 0);
  const got = await lmPick({title: `Same test as…`, name: s.name, tests, results: n, shown: nice});
  if (!got) return;
  const show = (j, target) => {
    const was = state.sel.has(s.name);
    storeRecords(j.records);
    if (was && target) state.sel.add(target);
    const t = target && SERIES.find(x => x.name === target);
    if (t) openLab(t); else labAgain();
  };
  try {
    const r = await lmCall(`profiles/${PROFILE.id}/labmaps?records=1`, "POST", {name: s.name, target: got.target, move: got.move});
    show(r, got.move ? got.target : null);
    toast(lmSaid(s.name, got.target, r.results, got.move), {action: "Undo", onAction: async () => {
      try { show(await lmCall(`profiles/${PROFILE.id}/labmaps/undo/${r.change}?records=1`, "POST"), null); announce("Undone: back as it was."); }
      catch (e) { toast(e.message); }
    }});
  } catch (e) { toast(e.message); }
}
