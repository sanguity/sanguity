/* Sanguity · unit preferences (#41): values shown in this person's units, exact conversions only.
   The server says which series can be shown in which unit, and by what factor (server/units.py): a lab series or
   watch metric carries conv = {choice: [unit, a, b]}, the value in that unit being value × a + b. The choice comes
   from the "units" setting (CFG.units: lab "conventional" | "si", weight "lb" | "kg", temperature "F" | "C", or
   "reported"). A series with no conversion for the choice stays as reported.
   applyUnits converts a series once, when the page loads it: its unit, every point's value, comparator text and
   reference range. What was reported stays on the series (s.uc.from) and on each point (p.orig), and ucOrig() says it
   beside every converted value (Trends, the Overview tiles, the heatmap, the drawer).
   ucValue / ucRange are the one conversion for anything else in a series' units (a reference range from elsewhere).
   Plain script, loaded in order by index.html before app.js; all files share one global scope. */
"use strict";
const UNIT_PART = {conventional: "lab", si: "lab", lb: "weight", kg: "weight", F: "temperature", C: "temperature"};
/* the conversion this person chose for a conv map, as {to, a, b}, or null */
function ucChoose(conv) {
  if (!conv || typeof conv !== "object") return null;
  const prefs = CFG.units || {};
  for (const [choice, c] of Object.entries(conv)) {
    if (!Array.isArray(c) || prefs[UNIT_PART[choice]] !== choice) continue;
    const [to, a, b] = c;
    if (typeof to === "string" && isFinite(a) && isFinite(b)) return {to, a: +a, b: +b};
  }
  return null;
}
const ucNum = v => v == null || v === "" ? null : typeof v === "number" ? v : /^\s*-?\d+(\.\d+)?\s*$/.test(String(v)) ? +v : null;
/* significant figures written in a number's text ("0.50" has 2, "95" has 2, "120" has 3) */
function ucSig(text) {
  const m = String(text).match(/-?(\d+)(?:\.(\d+))?/); if (!m) return 3;
  const digits = (m[1] + (m[2] || "")).replace(/^0+/, "");
  return Math.max(1, m[2] != null ? digits.length : digits.length || 1);
}
/* a converted number, to as many significant figures as it was reported with (at least 3) */
function ucFmt(x, sig) {
  if (!isFinite(x)) return "";
  if (x === 0) return "0";
  const dec = Math.max(0, Math.min(6, Math.max(3, sig) - 1 - Math.floor(Math.log10(Math.abs(x)))));
  return x.toFixed(dec);
}
function ucValue(uc, v) { const n = ucNum(v); return n == null || !uc ? v : n * uc.a + uc.b; }
/* a value as text ("<0.5", "95", "Positive") in the chosen unit; text that isn't a number stays as it is */
function ucText(uc, text) {
  const m = String(text ?? "").match(/^\s*([<>]=?|≤|≥)?\s*(-?\d+(?:\.\d+)?)\s*$/);
  return m ? (m[1] || "") + ucFmt(+m[2] * uc.a + uc.b, ucSig(m[2])) : text;
}
/* a reference range as text ("70-99", "<5.7", "3.5 – 5.0"), or null when it isn't one that can be converted */
function ucRange(uc, text) {
  const m = String(text ?? "").match(/^\s*([<>]=?|≤|≥)?\s*(-?\d+(?:\.\d+)?)\s*(?:(-|–|to)\s*(-?\d+(?:\.\d+)?))?\s*$/);
  if (!m || (m[1] && m[4])) return null;
  const f = n => ucFmt(+n * uc.a + uc.b, ucSig(n));
  return (m[1] || "") + f(m[2]) + (m[4] != null ? `–${f(m[4])}` : "");
}
/* convert series in place (lab series from DATA.labs, or watch series), each with its own conv */
function applyUnits(list) {
  for (const s of list) {
    const uc = s && !s.uc && ucChoose(s.conv);
    if (!uc) continue;
    s.uc = {...uc, from: s.unit};
    s.unit = uc.to;
    for (const p of s.pts) {
      const has = p[5] != null && p[5] !== "", shown = has ? String(p[5]) : p[1] == null ? "" : fmtNum(p[1]);
      // a reported text with its own unit ("37 Cel", from an importer that converted it) is kept whole
      p.orig = {v: shown, lo: p[2], hi: p[3], own: has && ucNum(shown) == null && !/^\s*([<>]=?|≤|≥)\s*[\d.-]+\s*$/.test(shown)};
      if (typeof p[1] === "number") p[1] = p[1] * uc.a + uc.b;
      for (const i of [2, 3]) { const n = ucNum(p[i]); if (n != null) p[i] = +ucFmt(n * uc.a + uc.b, ucSig(p[i])); }
      if (has) p[5] = !p.orig.own ? ucText(uc, shown) : typeof p[1] === "number" ? ucFmt(p[1], ucSig(shown)) : shown;
      for (const k of ["lo", "hi"]) if (typeof p[k] === "number") p[k] = p[k] * uc.a + uc.b;
    }
  }
}
/* "95 mg/dL, ref 70–99" as reported, for a point of a converted series; "" otherwise */
function ucOrig(s, p) {
  if (!s || !s.uc || !p || !p.orig) return "";
  const o = p.orig, r = o.lo != null || o.hi != null ? `, ref ${o.lo ?? ""}–${o.hi ?? ""}` : "";
  return `${o.v}${o.own ? "" : " " + s.uc.from}${r}`;
}
/* the drawer's result rows ([name, value, unit, ref range, flag]) in the chosen units, by the conversion of the
   series with that name and unit; each converted row keeps what was reported as row.orig = [value, unit, range] */
function applyUnitsToRows(labs, details) {
  const by = new Map();
  for (const s of labs) if (s.uc) by.set(s.name + "\u001f" + s.uc.from, s.uc);
  if (!by.size) return;
  for (const d of Object.values(details || {})) {
    if (!d || !Array.isArray(d.rows)) continue;
    for (const row of d.rows) {
      const uc = by.get(row[0] + "\u001f" + (row[2] || "")); if (!uc || row.orig) continue;
      const v = ucText(uc, row[1]);
      if (v === row[1] && ucNum(row[1]) == null && !/^\s*[<>≤≥]/.test(String(row[1] ?? ""))) continue;   // text: nothing to convert
      row.orig = [row[1], row[2], row[3]];
      const rr = row[3] ? ucRange(uc, row[3]) : row[3];
      row[1] = v; row[2] = uc.to;
      row[3] = rr == null ? `${row[3]} ${uc.from}` : rr;   // a range that can't be read stays, in its own unit
    }
  }
}
