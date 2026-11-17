/* Sanguity · where on the body (#161): the front and back outline a symptom's place is chosen on, and the body map of
   where symptoms have been. The regions are server/journal.py's REGIONS, with the same ids (tests/test_journal.py
   checks): ["id", "f" front or "b" back, its shape]. "Right" and "left" are the person's own, so on the front their
   right is on the screen's left, and on the back on its right; each outline says R and L at its sides.

   jBodyPicker(chosen, onchange): both outlines, each region a checkbox you tap or click, and from the keyboard the
   outline is one stop (arrow keys move between its regions, Space or Enter ticks one); under them the same choice
   as a list of checkboxes by part of the body, for screen readers and anyone who'd rather pick from a list.
   jBodyMap(counts, open): the outlines shaded by how many entries name each region, the number written in it, never
   by shading alone; a region with entries is a button (open(id)). The table beside it is journal.js's.
   Plain script, loaded before journal.js; all files share one global scope. */
"use strict";
/* shapes in a 100 x 222 box, drawn on the screen's left; "-" names its mirror on the right (x -> 100 - x) */
const JB_SHAPES = {
  head: "M40,17a10,12 0 1,0 20,0a10,12 0 1,0 -20,0Z",
  neck: [[45, 28], [55, 28], [56, 37], [44, 37]],
  sh: [[36, 38], [36, 54], [24, 52], [25, 44], [30, 39.5]],
  ua: [[24, 53], [35, 55], [32, 88], [21, 86]], fa: [[21, 87], [32, 89], [28, 120], [17, 118]],
  hd: [[17, 119], [28, 121], [28, 130], [24, 137], [18, 136], [15, 128]],
  th: [[35, 122], [50, 122], [49, 164], [38, 164]], kn: [[38, 165], [49, 165], [48.5, 177], [38.5, 177]],
  sn: [[38.5, 178], [48.5, 178], [47, 208], [40, 208]], ft: [[40, 209], [47, 209], [48.5, 218], [37, 218]],
  chest: [[36, 38], [44, 36.5], [56, 36.5], [64, 38], [64, 70], [36, 70]],
  uab: [[36, 71], [64, 71], [63, 88], [37, 88]], lab: [[37, 89], [63, 89], [64, 104], [36, 104]],
  groin: [[36, 105], [64, 105], [65, 121], [35, 121]],
  ub: [[36, 38], [44, 36.5], [56, 36.5], [64, 38], [64, 74], [36, 74]],
  lb: [[36, 75], [64, 75], [63, 88], [64, 102], [36, 102], [37, 88]],
  bt: [[36, 103], [49.5, 103], [49.5, 121], [35, 121]],
};
const JB_REGIONS = [   // [id, view, shape]; a shape "x-" is x's mirror
  ["head", "f", "head"], ["neck", "f", "neck"], ["r-shoulder", "f", "sh"], ["l-shoulder", "f", "sh-"],
  ["chest", "f", "chest"], ["upper-abdomen", "f", "uab"], ["lower-abdomen", "f", "lab"], ["groin", "f", "groin"],
  ["r-upper-arm", "f", "ua"], ["l-upper-arm", "f", "ua-"], ["r-forearm", "f", "fa"], ["l-forearm", "f", "fa-"],
  ["r-hand", "f", "hd"], ["l-hand", "f", "hd-"], ["r-thigh", "f", "th"], ["l-thigh", "f", "th-"],
  ["r-knee", "f", "kn"], ["l-knee", "f", "kn-"], ["r-shin", "f", "sn"], ["l-shin", "f", "sn-"],
  ["r-foot", "f", "ft"], ["l-foot", "f", "ft-"],
  ["back-head", "b", "head"], ["back-neck", "b", "neck"], ["l-shoulder-back", "b", "sh"], ["r-shoulder-back", "b", "sh-"],
  ["upper-back", "b", "ub"], ["lower-back", "b", "lb"], ["l-buttock", "b", "bt"], ["r-buttock", "b", "bt-"],
  ["l-upper-arm-back", "b", "ua"], ["r-upper-arm-back", "b", "ua-"], ["l-forearm-back", "b", "fa"], ["r-forearm-back", "b", "fa-"],
  ["l-hand-back", "b", "hd"], ["r-hand-back", "b", "hd-"], ["l-thigh-back", "b", "th"], ["r-thigh-back", "b", "th-"],
  ["l-knee-back", "b", "kn"], ["r-knee-back", "b", "kn-"], ["l-calf", "b", "sn"], ["r-calf", "b", "sn-"],
  ["l-heel", "b", "ft"], ["r-heel", "b", "ft-"],
];
const JB_SIDE = {r: "right", l: "left"};
/* the region's name, as server/journal.py's REGIONS says it */
function jbLabel(id) {
  const fixed = {head: "Head", neck: "Neck", chest: "Chest", "upper-abdomen": "Upper belly", "lower-abdomen": "Lower belly", groin: "Hips and groin",
    "back-head": "Back of the head", "back-neck": "Back of the neck", "upper-back": "Upper back", "lower-back": "Lower back"};
  if (fixed[id]) return fixed[id];
  const side = JB_SIDE[id[0]], Side = side[0].toUpperCase() + side.slice(1), k = id.slice(2);
  return {shoulder: `${Side} shoulder`, "upper-arm": `${Side} upper arm`, forearm: `${Side} forearm`, hand: `${Side} hand`,
    thigh: `Front of the ${side} thigh`, knee: `${Side} knee`, shin: `${Side} shin`, foot: `${Side} foot`, buttock: `${Side} buttock`,
    "shoulder-back": `Back of the ${side} shoulder`, "upper-arm-back": `Back of the ${side} upper arm`,
    "forearm-back": `Back of the ${side} forearm and elbow`, "hand-back": `Back of the ${side} hand`, "thigh-back": `Back of the ${side} thigh`,
    "knee-back": `Back of the ${side} knee`, calf: `${Side} calf`, heel: `${Side} heel and ankle`}[k] || id;
}
const JB_ORDER = JB_REGIONS.map(r => r[0]);
/* the list's parts of the body, each region once */
const JB_PARTS = [
  ["Head and neck", ["head", "back-head", "neck", "back-neck"]],
  ["Chest, belly and back", ["chest", "upper-abdomen", "lower-abdomen", "groin", "upper-back", "lower-back", "r-buttock", "l-buttock"]],
  ...["r", "l"].map(s => [`${JB_SIDE[s][0].toUpperCase() + JB_SIDE[s].slice(1)} arm`,
    ["shoulder", "shoulder-back", "upper-arm", "upper-arm-back", "forearm", "forearm-back", "hand", "hand-back"].map(k => `${s}-${k}`)]),
  ...["r", "l"].map(s => [`${JB_SIDE[s][0].toUpperCase() + JB_SIDE[s].slice(1)} leg`,
    ["thigh", "thigh-back", "knee", "knee-back", "shin", "calf", "foot", "heel"].map(k => `${s}-${k}`)]),
];
const jbPts = shape => {
  const mirror = shape.endsWith("-"), p = JB_SHAPES[mirror ? shape.slice(0, -1) : shape];
  return typeof p === "string" ? null : mirror ? p.map(([x, y]) => [100 - x, y]) : p;
};
function jbPath(shape) {
  const pts = jbPts(shape);
  return pts ? "M" + pts.map(p => p.join(",")).join("L") + "Z" : JB_SHAPES[shape.replace(/-$/, "")];
}
/* where a region's number goes: the middle of its points (the head's: its centre) */
function jbMiddle(shape) {
  const pts = jbPts(shape);
  if (!pts) return [50, 17];
  return [pts.reduce((a, p) => a + p[0], 0) / pts.length, pts.reduce((a, p) => a + p[1], 0) / pts.length];
}
/* one outline: "f" or "b". mark(path, id) dresses each region; extra(id, shape) -> more nodes drawn over it */
function jbOutline(view, mark, extra) {
  const [lt, rt] = view === "f" ? ["R", "L"] : ["L", "R"];
  const svg = el("svg:svg", {viewBox: "0 0 100 222", class: "jbody", "data-view": view},
    el("svg:text", {class: "jbside", x: 6, y: 12, "aria-hidden": "true", text: lt}), el("svg:text", {class: "jbside", x: 94, y: 12, "text-anchor": "end", "aria-hidden": "true", text: rt}));
  for (const [id, v, shape] of JB_REGIONS) {
    if (v !== view) continue;
    const p = el("svg:path", {class: "jreg", d: jbPath(shape), "data-region": id});
    mark(p, id);
    svg.append(p, ...(extra ? extra(id, shape) : []));
  }
  return svg;
}
const jbFigure = (view, svg) => el("figure", {class: "jbfig"}, svg, el("figcaption", {"aria-hidden": "true", text: view === "f" ? "Front" : "Back"}));
const jbSay = ids => ids.length ? ids.map(jbLabel).join(", ") : "Nowhere chosen";

/* the picker for a form: returns {node, get()}; chosen: region ids */
function jBodyPicker(chosen, onchange) {
  const sel = new Set(chosen || []);
  const uid = Math.random().toString(36).slice(2, 8);
  const said = el("p", {class: "jbsaid", "aria-live": "polite"});
  const boxes = new Map();
  const sync = () => {
    for (const p of node.querySelectorAll(".jreg")) { const on = sel.has(p.dataset.region); p.setAttribute("aria-checked", on); p.classList.toggle("on", on); }
    for (const [id, b] of boxes) b.checked = sel.has(id);
    said.textContent = sel.size ? "Chosen: " + jbSay(JB_ORDER.filter(id => sel.has(id))) : "Nowhere chosen yet: tap the outline, or choose from the list.";
    clear.hidden = !sel.size;
    if (onchange) onchange([...sel]);
  };
  const flip = id => { sel.has(id) ? sel.delete(id) : sel.add(id); sync(); };
  const outline = view => {
    const svg = jbOutline(view, (p, id) => {
      p.setAttribute("role", "checkbox"); p.setAttribute("aria-label", jbLabel(id)); p.setAttribute("tabindex", "-1");
      p.append(el("svg:title", {text: jbLabel(id)}));
      p.addEventListener("click", () => { flip(id); p.focus({preventScroll: true}); });
    });
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", view === "f" ? "Front of the body: tick where, with the arrow keys and Space" : "Back of the body: tick where, with the arrow keys and Space");
    const regs = [...svg.querySelectorAll(".jreg")];
    regs[0].setAttribute("tabindex", "0");   // the outline is one stop; the arrow keys move inside it
    svg.addEventListener("keydown", ev => {
      const i = regs.indexOf(ev.target); if (i < 0) return;
      const k = ev.key, n = regs.length;
      const to = k === "ArrowRight" || k === "ArrowDown" ? (i + 1) % n : k === "ArrowLeft" || k === "ArrowUp" ? (i - 1 + n) % n : k === "Home" ? 0 : k === "End" ? n - 1 : null;
      if (to != null) { ev.preventDefault(); regs[i].setAttribute("tabindex", "-1"); regs[to].setAttribute("tabindex", "0"); regs[to].focus(); return; }
      if (k === " " || k === "Enter") { ev.preventDefault(); flip(regs[i].dataset.region); }
    });
    return jbFigure(view, svg);
  };
  const list = el("details", {class: "jblist"}, el("summary", {text: "Choose from a list instead"}),
    ...JB_PARTS.map(([part, ids]) => el("fieldset", {}, el("legend", {text: part}), ...ids.map(id => {
      const b = el("input", {type: "checkbox", id: `jb-${id}-${uid}`, onchange: () => { b.checked ? sel.add(id) : sel.delete(id); sync(); }});
      boxes.set(id, b);
      return el("label", {for: b.id}, b, " " + jbLabel(id));
    }))));
  const clear = el("button", {type: "button", class: "btn jbclear", text: "Clear", onclick: () => { sel.clear(); sync(); }});
  const node = el("div", {class: "jbpick"}, el("div", {class: "jbfigs"}, outline("f"), outline("b")), el("div", {class: "jbsaid-row"}, said, clear), list);
  sync();
  return {node, get: () => JB_ORDER.filter(id => sel.has(id))};
}

/* the body map: counts {id: {n, worst, last}}; open(id): show that region's entries. Shading in four steps, the number
   in the region as well; regions without entries stay outlined. */
function jBodyMap(counts, open) {
  const max = Math.max(1, ...Object.values(counts).map(c => c.n));
  const step = n => n <= 0 ? 0 : Math.min(4, Math.ceil(n / max * 4));
  const figs = ["f", "b"].map(view => {
    const svg = jbOutline(view, (p, id) => {
      const c = counts[id];
      if (!c) { p.classList.add("none"); return; }
      p.classList.add("has", "s" + step(c.n));
      const words = `${jbLabel(id)}: ${c.n} entr${c.n === 1 ? "y" : "ies"}` + (c.worst != null ? `, the worst ${c.worst}/10` : "");
      Object.entries({tabindex: 0, role: "button", "aria-label": words + ". Show them in the list"}).forEach(([k, v]) => p.setAttribute(k, v));
      p.append(el("svg:title", {text: words}));
      p.addEventListener("click", () => open(id));
      p.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(id); } });
    }, (id, shape) => {
      const c = counts[id]; if (!c) return [];
      const [x, y] = jbMiddle(shape);
      return [el("svg:text", {class: "jbn" + (step(c.n) >= 3 ? " dark" : ""), x, y: y + 2.6, "text-anchor": "middle", "aria-hidden": "true", text: c.n})];
    });
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", view === "f" ? "Front of the body" : "Back of the body");
    return jbFigure(view, svg);
  });
  return el("div", {class: "jbfigs jbmap"}, ...figs);
}
