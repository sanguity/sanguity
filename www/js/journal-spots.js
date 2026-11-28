/* Sanguity · spots (#1139): one place on the body over time, a mole, a rash or a wound. A journal photo can be of a
   named spot, tagged in its entry's form (journal.js jPhotosPart: an earlier spot's name or a new one); the records
   list each photo's spot as {id, name} (server/journal_photos.py). A spot's drawer shows its photos in date order:
   any two side by side, or one over the other with a slider between them, each with its date and its entry's note.
   It's only for looking: nothing here measures the pictures or says anything about them. Owners and editors rename
   a spot, merge it into another and take a photo off it. Its few layout rules are set here, on the elements, rather
   than in app.css (the static budget, docs/performance.md). Plain script, loaded by index.html after journal.js. */
"use strict";
/* the spots, from the entries: [{id, name, shots: [{p: the photo, e: its entry's event, i}]}] by name, each spot's
   photos oldest first */
function jSpots() {
  const by = new Map();
  for (const e of JOURNAL) (e.j.photos || []).forEach((p, i) => {
    if (!p.spot) return;
    if (!by.has(p.spot.id)) by.set(p.spot.id, {id: p.spot.id, name: p.spot.name, shots: []});
    by.get(p.spot.id).shots.push({p, e, i});
  });
  const out = [...by.values()];
  for (const s of out) s.shots.sort((a, b) => a.e.ts - b.e.ts || a.e.j.id - b.e.j.id || a.i - b.i);
  return out.sort((a, b) => a.name.localeCompare(b.name));
}
const jSpotNames = () => jSpots().map(s => s.name);
const jShotWhen = x => jWhen(x.e.j);
const jPhotosN = n => `${n} photo${n === 1 ? "" : "s"}`;
/* the spots as buttons, for the journal's card and its list in the panel; none when no photo is of one */
function jSpotsLine() {
  const all = jSpots();
  return all.length ? el("div", {class: "btns jspots", role: "group", "aria-label": "Spots"}, el("span", {class: "hint", text: "Spots:"}),
    ...all.map(s => el("button", {class: "btn", type: "button", text: `${s.name} (${s.shots.length})`, "aria-label": `${s.name}, ${jPhotosN(s.shots.length)}`,
      onclick: () => openSpot(s.id)}))) : null;
}
/* a photo with its date, what it shows in the person's words and its entry's note: id names the caption, for the
   slider to be described by */
function jShotCaption(x, id, lead) {
  return el("figcaption", {id}, el("b", {class: "jshot-when", text: (lead ? lead + ": " : "") + jShotWhen(x)}),
    x.p.alt ? el("div", {text: x.p.alt}) : null, x.e.j.note ? el("div", {class: "jnote m-0", text: x.e.j.note}) : null);
}
/* cls: "jshot-fit" for a photo filling its frame (.jshot-frame, app.css), whole */
const jShotImg = (x, cls) => el("img", {src: jPhotoUrl(x.p), alt: x.p.alt || `Photo of ${jShotWhen(x)}`, class: cls || null});

/* two photos of the spot: side by side, or one over the other with a slider (an input type=range: arrow keys, touch,
   said as both photos' dates). a, b: indexes into s.shots; slider: which view */
function jSpotCompare(s, uid) {
  const v = {a: 0, b: s.shots.length - 1, slider: false, at: 50};
  const box = el("section", {class: "jspot-cmp", "aria-labelledby": `jsc-${uid}`});
  const pick = (k, label) => {
    const sel = el("select", {id: `jsc-${uid}-${k}`}, ...s.shots.map((x, i) => el("option", {value: i, text: `${jShotWhen(x)} · ${jTitle(x.e.j)}`})));
    sel.value = v[k];
    sel.onchange = () => { v[k] = +sel.value; draw(); };
    return el("div", {class: "jfield"}, el("label", {for: sel.id, text: label}), sel);
  };
  const views = el("div", {class: "seg", role: "group", "aria-label": "Show the two photos"},
    ...[["Side by side", false], ["Slider", true]].map(([t, on]) => el("button", {type: "button", text: t, "aria-pressed": v.slider === on,
      onclick: ev => { v.slider = on; for (const b of views.children) b.setAttribute("aria-pressed", b === ev.currentTarget); draw(); }})));
  const shown = el("div");
  const draw = () => {
    const A = s.shots[v.a], B = s.shots[v.b];
    if (!v.slider) {
      const fig = (x, n) => el("figure", {class: "jpic jshot"},
        el("a", {href: jPhotoUrl(x.p), target: "_blank", rel: "noopener", "data-tip": "Open it full size", class: "jshot-frame"}, jShotImg(x, "jshot-fit")),
        jShotCaption(x, `jsc-${uid}-c${n}`));
      return shown.replaceChildren(el("div", {class: "jpics jshots"}, fig(A, 1), fig(B, 2)));
    }
    // the first photo over the second, shown left of the line; the slider moves the line
    const frame = el("div", {class: "jshot-frame"}, jShotImg(B, "jshot-fit"), jShotImg(A, "jshot-fit jshot-top"), el("span", {class: "jshot-line", "aria-hidden": "true"}));
    const range = el("input", {type: "range", id: `jsc-${uid}-r`, min: 0, max: 100, step: 5,
      "aria-label": `Slider between the photos of ${jShotWhen(A)} and ${jShotWhen(B)}`, "aria-describedby": `jsc-${uid}-c1 jsc-${uid}-c2`});
    range.value = v.at;
    const move = () => {
      v.at = +range.value;
      frame.style.setProperty("--at", v.at + "%");   // where the line is, and how much of the first photo shows (app.css)
      range.setAttribute("aria-valuetext", `${jShotWhen(A)} on the left ${v.at}%, ${jShotWhen(B)} on the right ${100 - v.at}%`);
    };
    range.addEventListener("input", move);
    shown.replaceChildren(el("figure", {class: "jpic jshot-slide"},
      frame, range,
      el("div", {class: "jpics jshots m-0"}, ...[[A, 1, "Left"], [B, 2, "Right"]].map(([x, n, lead]) =>
        el("div", {class: "jpic jshot"}, jShotCaption(x, `jsc-${uid}-c${n}`, lead))))));
    move();
  };
  draw();
  box.append(el("div", {class: "sect", id: `jsc-${uid}`, text: "Two photos"}), pick("a", "First photo"), pick("b", "Second photo"), views, shown);
  return box;
}

/* a spot's drawer: the two photos to look at, then every photo of it, oldest first */
function openSpot(id) {
  const s = jSpots().find(x => x.id === id);
  if (!s) return journalHome();
  state.selEv = null;
  const n = s.shots.length, uid = jUid(), msg = el("p", {class: "hint edit-msg", role: "status"});
  const body = jDrawer(s.name, `Spot · ${jPhotosN(n)} · ` + (n > 1 ? `${fmtD(s.shots[0].e.ts)} – ${fmtD(s.shots[n - 1].e.ts)}` : fmtD(s.shots[0].e.ts)));
  if (jCanWrite()) drawerMenu([{text: "Rename", run: () => jSpotRename(s, msg)},
    jSpots().length > 1 ? {text: "Merge with another spot", run: () => jSpotMerge(s, msg)} : null], "Change this spot");
  if (n > 1) body.append(jSpotCompare(s, uid));
  else body.append(el("p", {class: "hint", text: "Tag another photo with this spot, in its journal entry, to see the two side by side."}));
  body.append(el("div", {class: "sect", text: `Every photo of it (${n}), oldest first`}),
    el("div", {class: "jpics", role: "list"}, ...s.shots.map((x, k) => el("figure", {class: "jpic", role: "listitem"},
      el("a", {href: jPhotoUrl(x.p), target: "_blank", rel: "noopener", "data-tip": "Open it full size"}, jShotImg(x)),
      jShotCaption(x, `jsl-${uid}-${k}`),
      el("button", {class: "linkish", type: "button", text: "Open the entry", "aria-label": `Open the entry of ${jShotWhen(x)}: ${jTitle(x.e.j)}`, onclick: () => openEvent(x.e)}),
      jCanWrite() ? el("button", {class: "btn", type: "button", text: "Take off this spot", "aria-label": `Take the photo of ${jShotWhen(x)} off this spot`,
        onclick: () => jSpotSend(`/photos/${x.p.id}`, {method: "DELETE"}, msg, n > 1 ? s.id : null, "Taken off the spot. The photo stays with its entry.")}) : null))),
    el("div", {class: "btns dgo"}, el("button", {class: "btn", type: "button", text: "All journal entries", onclick: () => journalHome(true)})), msg);
  showDrawer();
}
/* a change to the spots: the reply's entries put in (store.js), then the spot again (back: its id; null: the journal) */
async function jSpotSend(path, opts, msg, back, said) {
  sayIn(msg, "Saving…");
  let r, j;
  try { r = await api(`profiles/${PROFILE.id}/journal/spots${path}`, opts); j = await r.json().catch(() => ({})); }
  catch (err) { sayIn(msg, "Couldn't reach the server: " + err.message, true); return false; }
  if (!r.ok) { sayIn(msg, typeof j.detail === "string" ? j.detail : `Couldn't save the change (${r.status}).`, true); return false; }
  storePut("journal", j);
  if (back != null && jSpots().some(x => x.id === back)) openSpot(back); else journalHome();
  announce(said);
  return true;
}
async function jSpotRename(s, msg) {
  const input = el("input", {type: "text", id: "jspotName", maxlength: 80, autocomplete: "off"}); input.value = s.name;
  if (!await ask({title: `Rename “${s.name}”`, message: [el("label", {for: input.id, text: "The spot's name"}), input], action: "Rename", field: input})) return;
  if (input.value.trim() && input.value.trim() !== s.name)
    jSpotSend(`/${s.id}`, {method: "PUT", body: JSON.stringify({name: input.value})}, msg, s.id, "Spot renamed.");
}
async function jSpotMerge(s, msg) {
  const others = jSpots().filter(x => x.id !== s.id);
  const sel = el("select", {id: "jspotInto"}, ...others.map(x => el("option", {value: x.id, text: `${x.name} (${x.shots.length})`})));
  if (!await ask({title: `Merge “${s.name}” with another spot`, action: "Merge", field: sel, message: [
    `Its ${jPhotosN(s.shots.length)} become photos of the spot you pick, which keeps its name.`, el("label", {for: sel.id, text: "Merge with"}), sel]})) return;
  jSpotSend(`/${s.id}/merge`, {method: "POST", body: JSON.stringify({into: +sel.value})}, msg, +sel.value, "Spots merged.");
}
