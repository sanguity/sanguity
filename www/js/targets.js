/* Sanguity · targets of your own for home readings (#234). server/targets.py keeps them, as the profile setting
   person.targets: {measure: target}, each the care team's, typed in on Settings → About this person by an owner or
   editor, with who set it ("by") and when ("on"). Values are kept in one unit each (glucose mg/dL, temperature °C,
   weight kg; blood pressure mmHg, oxygen %), and shown in the units each page shows the readings in.

   Sanguity never suggests a target: with none entered nothing here draws anything, and the standard published
   ranges stay labelled as their publisher's. A reading at home is above (▲) or below (▼) its target compared as it's
   shown; readings taken at visits keep only their own report's flags.

   On the records page (health.js, overview.js): targetOf(k), tgtIn(k, unit) [low, high] in a unit, tgtSide(v, lo, hi,
   places) "h" / "l" / "", tgtMark(V) the readings' flags, tgtLine(...) a card's line about its target, tgtLayers(...)
   a chart's band. On Settings (about.js, #267): targetsCard(p, a, send), the editor. Plain script; all files share one scope. */
"use strict";
const TGT_MMOL = 18.0156;      // mg/dL in 1 mmol/L of glucose (server/glucose.py MGDL_PER_MMOL)
const TGT_LB = 0.45359237;     // kg in a pound
/* each measure: its name, the units it's typed in (the first the one it's kept in), places shown, and its limits in
   words (server/targets.py LIMITS) */
const TGT = {
  bp: {name: "Blood pressure", what: "readings at home", units: ["mmHg"], places: 0, card: "#h-bp"},
  glucose: {name: "Blood glucose, meter readings", what: "meter readings", units: ["mg/dL", "mmol/L"], places: 0, card: "#h-glucose"},
  cgm: {name: "CGM time in range", what: "CGM readings", units: ["mg/dL", "mmol/L"], places: 0, card: "#h-glucose"},
  temp: {name: "Body temperature", what: "readings at home", units: ["C", "F"], places: 1, card: "#h-temp"},
  spo2: {name: "Blood oxygen", what: "readings at home", units: ["%"], places: 0, card: "#h-oxygen"},
  weight: {name: "Weight", what: "smart scale weights", units: ["kg", "lb"], places: 1, card: "#h-body"},
};
const TGT_UNIT = {C: "°C", F: "°F"};
const tgtLower = k => k === "cgm" ? TGT[k].name : TGT[k].name.toLowerCase();   // "blood pressure", "CGM time in range"
const TGT_WORDS = {h: "Above your target", l: "Below your target"};
const tgtWord = f => f === "h" ? "▲ above your target" : f === "l" ? "▼ below your target" : "";
function targetsOf(cfg) {
  const t = (((cfg !== undefined ? cfg : (typeof CFG !== "undefined" ? CFG : null)) || {}).person || {}).targets;
  return t && typeof t === "object" && !Array.isArray(t) ? t : {};
}
const targetOf = (k, cfg) => targetsOf(cfg)[k] || null;
/* a value kept in the measure's own unit, in unit ("mmol/L", "F", "°F", "lb"...); and back */
function tgtConv(k, v, unit) {
  if (v == null) return null;
  const u = String(unit || "").replace("°", "");
  if ((k === "glucose" || k === "cgm") && /mmol/i.test(u)) return v / TGT_MMOL;
  if (k === "temp" && u === "F") return v * 9 / 5 + 32;
  if (k === "weight" && u === "lb") return v / TGT_LB;
  return v;
}
function tgtCanon(k, v, unit) {
  if (v == null) return null;
  const u = String(unit || "").replace("°", "");
  if ((k === "glucose" || k === "cgm") && /mmol/i.test(u)) return v * TGT_MMOL;
  if (k === "temp" && u === "F") return (v - 32) * 5 / 9;
  if (k === "weight" && u === "lb") return v * TGT_LB;
  return v;
}
/* [low, high] of a measure's target (part "sys" or "dia" for blood pressure) in unit, or null when it has none */
function tgtIn(k, unit, part = "", cfg) {
  const t = targetOf(k, cfg); if (!t) return null;
  const lo = part ? t[part + "Low"] : t.low, hi = part ? t[part + "High"] : t.high;
  return lo == null && hi == null ? null : [tgtConv(k, lo, unit), tgtConv(k, hi, unit)];
}
/* "h" above, "l" below, "" in it (or no target): compared as each is shown, at places */
function tgtSide(v, lo, hi, places = 0) {
  if (v == null || !isFinite(v)) return "";
  const r = x => Math.round(x * 10 ** places) / 10 ** places;
  return hi != null && r(v) > r(hi) ? "h" : lo != null && r(v) < r(lo) ? "l" : "";
}
const tgtNum = (v, places) => places ? (+v.toFixed(places)).toFixed(places).replace(/\.0+$/, "") : String(Math.round(v));
/* "80–130 mg/dL", "up to 130 mg/dL", "at least 92%" */
function tgtRange(lo, hi, places, unit) {
  const f = v => tgtNum(v, places), sp = unit === "%" ? "" : " ";
  return lo != null && hi != null ? `${f(lo)}–${f(hi)}${sp}${unit}` : hi != null ? `up to ${f(hi)}${sp}${unit}` : `at least ${f(lo)}${sp}${unit}`;
}
/* blood pressure's: "up to 130/80 mmHg", or each part apart ("systolic 100–130, diastolic up to 80 mmHg") */
function tgtBpText(t) {
  const [sl, sh, dl, dh] = ["sysLow", "sysHigh", "diaLow", "diaHigh"].map(k => t[k] ?? null);
  if (sl == null && dl == null && sh != null && dh != null) return `up to ${sh}/${dh} mmHg`;
  if (sh == null && dh == null && sl != null && dl != null) return `at least ${sl}/${dl} mmHg`;
  return [["systolic", sl, sh], ["diastolic", dl, dh]].filter(([, a, b]) => a != null || b != null)
    .map(([n, a, b]) => `${n} ${tgtRange(a, b, 0, "").trim()}`).join(", ") + " mmHg";
}
/* a target in words, in unit: "80–130 mg/dL" */
function tgtText(k, unit, cfg) {
  const t = targetOf(k, cfg); if (!t) return "";
  if (k === "bp") return tgtBpText(t);
  const shown = TGT_UNIT[String(unit).replace("°", "")] || unit;
  return tgtRange(tgtConv(k, t.low, unit), tgtConv(k, t.high, unit), k === "glucose" || k === "cgm" ? (/mmol/i.test(unit) ? 1 : 0) : TGT[k].places, shown);
}
/* who set it and when, in words: "set by Dr. Rowan Vale, Mar 3, 2026", or "" */
function tgtWho(t) {
  const on = t && t.on ? fmtD(dayTs(t.on)) : "";
  return t && t.by ? `set by ${t.by}${on ? ", " + on : ""}` : on ? `set on ${on}` : "";
}

/* ---- the records page */
/* the readings' flags against the person's targets (hVitals' lists, in the units they're shown in): r.tf [flag of
   each value]; only readings taken at home */
function tgtMark(V) {
  const mark = (rows, k, unit, parts, places) => {
    const bands = parts.map(p => tgtIn(k, unit, p));
    if (!bands.some(Boolean)) return;
    for (const r of rows) r.tf = r.v.map((v, i) => bands[i] ? tgtSide(v, ...bands[i], places) : "");
  };
  mark(V.bp.home, "bp", "mmHg", ["sys", "dia"], 0);
  mark(V.temp.home, "temp", V.temp.unit, [""], 1);
  mark(V.spo2.home, "spo2", "%", [""], 0);   // each day's average (the day's low beside it isn't compared)
  for (const r of V.spo2.home) if (r.tf) r.tf = [r.tf[0]];
  mark(V.spo2.typed, "spo2", "%", [""], 0);
  return V;
}
/* a table cell [text, flag, words] (charts.js chartTable): a clinic's flag as its report says it, else the target's */
const tgtCell = (text, r, i) => r.f && r.f[i] ? [text, r.f[i]] : r.tf && r.tf[i] ? [text, r.tf[i], TGT_WORDS[r.tf[i]]] : [text, ""];
/* "4 of 62 readings at home in this period above it, 1 below it" from each one's flag ("h", "l" or "") */
function tgtCount(flags, what) {
  const n = flags.length, hi = flags.filter(f => f === "h").length, lo = flags.filter(f => f === "l").length;
  if (!n) return `No ${what} in this period.`;
  if (n === 1) what = what.replace(/s\b/, "");   // "1 of 1 reading at home"
  if (!hi && !lo) return n === 1 ? `The ${what} in this period isn't outside it.` : `None of the ${n} ${what} in this period outside it.`;
  return `${hi ? `${hi} of ${n} ${what} in this period above it` : `${lo} of ${n} ${what} in this period below it`}${hi && lo ? `, ${lo} below it` : ""}.`;
}
/* a card's line about its target, under its hint: what it is, who set it, how many of the period's readings are
   outside it; and what ▲ and ▼ mean at home */
function tgtLine(card, k, unit, flags, extra = "") {
  const t = targetOf(k); if (!t) return null;
  const who = tgtWho(t);
  const p = el("p", {class: "hint tgt-line", "data-target": k}, el("b", {text: `Your target, as your care team gave it: ${tgtText(k, unit)}`}),
    who ? ` (${who}).` : ".", " ", flags ? tgtCount(flags, TGT[k].what) : "", extra,
    flags && flags.some(Boolean) ? " ▲ or ▼ marks a reading at home above or below it." : "");
  const at = card.querySelector(":scope > .hint"); if (at) at.after(p); else card.append(p);
  return p;
}
/* a chart layer for hChart: the target as a band (both ends) or a line (one), labelled */
const tgtLayer = (b, label = "Your target") => b ? {type: "target", low: b[0], high: b[1], label} : null;
const tgtKey = (label = "Your target, as your care team gave it") => el("span", {}, el("i", {class: "tgt-key"}), label);

/* ---- Settings → About this person: the editor (owners and editors), or the list (viewers) */
/* p: the profile; a: GET person's answer; pref: this person's Units choices (settings/user units); send(path, method,
   payload, said, focusId, quiet) as about.js's, the words to say once done (naming the profile if another is shown
   by then, #377); undo(left, path, method, payload, said, since,
   focusId) about.js's, for an Undo (#367) */
function targetsCard(p, a, pref, send, undo) {
  const T = a.targets || {}, edit = a.can_change, box = el("div", {class: "tgt-edit"});
  const unitFor = k => {   // the unit each is typed in: the person's Units choice, else as it was typed, else the first
    const u = pref || {}, typed = (T[k] || {}).unit;
    if (k === "glucose" || k === "cgm") return u.lab === "si" ? "mmol/L" : u.lab === "conventional" ? "mg/dL" : typed || "mg/dL";
    if (k === "temp") return u.temperature === "F" || u.temperature === "C" ? u.temperature : typed || "C";
    if (k === "weight") return u.weight === "kg" || u.weight === "lb" ? u.weight : typed || "lb";
    return TGT[k].units[0];
  };
  const placesFor = (k, unit) => (k === "glucose" || k === "cgm") ? (unit === "mmol/L" ? 1 : 0) : TGT[k].places;
  const shownUnit = u => TGT_UNIT[u] || u;
  for (const k of Object.keys(TGT)) {
    const t = T[k], unit = unitFor(k), pl = placesFor(k, unit), id = "tgt-" + k;
    const now = t ? (k === "bp" ? tgtBpText(t) : tgtRange(tgtConv(k, t.low, unit), tgtConv(k, t.high, unit), pl, shownUnit(unit))) : "";
    const head = el("h3", {class: "sub", id: id + "-hd", tabindex: -1, text: TGT[k].name});
    const said = el("p", {class: "muted tgt-now", text: t ? `Your target: ${now}${tgtWho(t) ? ` (${tgtWho(t)})` : ""}.` : "No target entered."});
    if (!edit) { box.append(el("section", {class: "tgt-one", "aria-labelledby": id + "-hd"}, head, said)); continue; }
    const num = (name, label, v) => el("label", {class: "about-num"}, label,
      el("input", {type: "number", name, step: "any", inputmode: "decimal", value: v == null ? "" : String(+v.toFixed(pl))}));
    const fields = k === "bp"
      ? [["sys", "Systolic"], ["dia", "Diastolic"]].map(([part, n]) => el("fieldset", {class: "tgt-pair"}, el("legend", {text: `${n} (mmHg)`}),
          num(part + "Low", "Low end", t ? t[part + "Low"] : null), num(part + "High", "High end", t ? t[part + "High"] : null)))
      : [el("fieldset", {class: "tgt-pair"}, el("legend", {text: `In ${shownUnit(unit)}`}),
          num("low", "Low end", t ? tgtConv(k, t.low, unit) : null), num("high", "High end", t ? tgtConv(k, t.high, unit) : null))];
    const by = el("input", {type: "text", name: "by", maxlength: 80, value: t ? t.by || "" : "", autocomplete: "off"});
    const on = el("input", {type: "date", name: "on", value: t ? t.on || "" : "", min: "1900-01-01", max: isoDay(Date.now())});
    const form = el("form", {class: "tgt-form", "aria-labelledby": id + "-hd", onsubmit: ev => { ev.preventDefault();
      const f = n => { const i = form.elements[n]; return i && i.value !== "" ? +i.value : null; };
      const body = k === "bp" ? {sysLow: f("sysLow"), sysHigh: f("sysHigh"), diaLow: f("diaLow"), diaHigh: f("diaHigh")} : {low: f("low"), high: f("high"), unit};
      send(`profiles/${p.id}/person`, "PUT", {targets: {[k]: {...body, by: by.value.trim(), on: on.value || ""}}},
        got => `${t ? "Changed" : "Saved"} the target for ${tgtLower(k)}: ${k === "bp" ? tgtBpText(got.targets[k]) : tgtRange(tgtConv(k, got.targets[k].low, unit), tgtConv(k, got.targets[k].high, unit), pl, shownUnit(unit))}.`,
        id + "-save"); }},
      ...fields,
      el("div", {class: "tgt-who"}, el("label", {class: "about-num"}, "Set by (optional)", by), el("label", {class: "about-num"}, "On (optional)", on)),
      el("div", {class: "about-row"}, el("button", {class: "btn", type: "submit", id: id + "-save", text: t ? "Save changes" : "Save target"}),
        t ? el("button", {class: "btn small", type: "button", id: id + "-clear", text: "Clear", "aria-label": `Clear the target for ${tgtLower(k)}`,
          onclick: async () => {   // at once, with an Undo that enters it again as it was, in the unit it was typed in (#263), if none was since (#367)
            const said = `Cleared the target for ${tgtLower(k)}.`, was = k === "bp" ? {...t} : {...t, low: tgtConv(k, t.low, t.unit), high: tgtConv(k, t.high, t.unit)};
            const done = await send(`profiles/${p.id}/person`, "PUT", {targets: {[k]: null}}, said, id + "-hd", true);
            if (done)
              toast(`${done} It was ${now}.`, {action: "Undo", onAction: () => undo(got => !(got.targets || {})[k],   // none entered since
                `profiles/${p.id}/person`, "PUT", {targets: {[k]: was}}, `Undone: the target for ${tgtLower(k)} is back.`,
                `A target for ${tgtLower(k)} was entered since: it's left as it is.`, id + "-hd")}); }}) : null));
    box.append(el("section", {class: "tgt-one", "aria-labelledby": id + "-hd"}, head, said, form));
  }
  return el("div", {},
    el("p", {class: "hint", text: "For readings taken at home: a target each, as your care team gave it. Optional; with none entered, nothing changes."}),
    moreAbout("Body and activity draws each one on its chart, marks a reading at home above or below it with ▲ or ▼ and words, and says how many in the period were. " +
      "CGM time in your own range is shown beside the standard published range, each labelled. A visit summary or share link includes them only when you tick “Your targets for home readings”. " +
      "Sanguity never suggests a target or says what one should be. Values are in your units (Settings → Units). This isn't medical advice."),
    box);
}
