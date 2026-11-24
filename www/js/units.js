/* Sanguity · unit preferences (#41): values shown in this person's units, exact conversions only.
   The server says which series can be shown in which unit, and by what factor (server/units.py): a lab series or
   watch metric carries conv = {choice: [unit, a, b]}, the value in that unit being value × a + b. The choice comes
   from the "units" setting (CFG.units: lab "conventional" | "si", weight "lb" | "kg", temperature "F" | "C", height
   "cm" | "ftin" (#194: a height series in inches on a chart; feet and inches where one height is said), or "reported"). A series with no conversion for the choice stays as reported.
   applyUnits converts a series once, when the page loads it: its unit, every point's value, comparator text and
   reference range. What was reported stays on the series (s.uc.from) and on each point (p.orig), and ucOrig() says it
   beside every converted value (Trends, the Overview tiles, the heatmap, the drawer).
   ucValue / ucRange are the one conversion for anything else in a series' units (a reference range from elsewhere).
   Plain script, loaded in order by index.html before app.js; all files share one global scope. */
"use strict";
const UNIT_PART = {conventional: "lab", si: "lab", lb: "weight", kg: "weight", F: "temperature", C: "temperature", cm: "height", ftin: "height"};   // height: #194
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
/* a number as given, not as a bound ("95", "4,5", 95), else null; the significant figures a value is written with ("0.50"
   has 2, "95" 2, "120" 3; 3 where it isn't one). Each by ranges.js valueRead, the page's one reader of a value (#874) */
const ucNum = v => { const r = v == null || v === "" ? null : valueRead(v); return r && !r.c ? r.x : null; };
const ucSig = text => valueRead(text)?.sig ?? 3;
/* a converted number, to as many significant figures as it was reported with (at least 3) */
function ucFmt(x, sig) {
  if (!isFinite(x)) return "";
  if (x === 0) return "0";
  const dec = Math.max(0, Math.min(6, Math.max(3, sig) - 1 - Math.floor(Math.log10(Math.abs(x)))));
  return x.toFixed(dec);
}
function ucValue(uc, v) { const n = ucNum(v); return n == null || !uc ? v : n * uc.a + uc.b; }
/* a value as text ("<0.5", "95", "=<5", "> OR = 60", "Positive") in the chosen unit, its bound as printed; text that
   isn't a number stays as it is (ranges.js valueRead, #874) */
function ucText(uc, text) {
  const r = valueRead(text);
  return r ? r.b + ucFmt(r.x * uc.a + uc.b, r.sig) : text;
}
/* a reference range as text ("70-99", "<5.7", "3.5 – 5.0", "≤ 100"), or null when it isn't one that can be converted:
   a limit, or two numbers either side of "-", "–" or "to", each read by ranges.js valueRead (#874) */
function ucRange(uc, text) {
  const f = r => ucFmt(r.x * uc.a + uc.b, r.sig), one = valueRead(text);
  if (one) return one.b + f(one);
  const m = /^\s*(-?[^\s–-][^–-]*?)\s*(?:-|–|\bto\b)\s*(\S.*?)\s*$/i.exec(String(text ?? "")), a = m && valueRead(m[1]), b = m && valueRead(m[2]);
  return a && b && !a.c && !b.c ? `${f(a)}–${f(b)}` : null;
}
/* convert series in place (lab series from DATA.labs, or watch series), each with its own conv */
function applyUnits(list) {
  for (const s of list) {
    const uc = s && !s.uc && ucChoose(s.conv);
    if (!uc) continue;
    s.uc = {...uc, from: s.unit};
    s.unit = uc.to;
    for (const p of s.pts) {
      const has = p[5] != null && p[5] !== "", shown = has ? String(p[5]) : p[1] == null ? "" : fmtNum(p[1]), read = has ? valueRead(shown) : null;
      // a reported text with its own unit ("37 Cel", from an importer that converted it) is kept whole: text that isn't a
      // value as ranges.js valueRead reads one ("4,5", "=<5" and "> OR = 60" are, #874: they were said as their number)
      // its report's range said as Labs says one (ranges.js rangeWords, #782), not one from settings, which isn't the report's
      p.orig = {v: shown, ref: p.rs === "settings" ? null : rangeWords(p), own: has && !read};
      if (typeof p[1] === "number") p[1] = p[1] * uc.a + uc.b;
      for (const i of [2, 3]) { const n = ucNum(p[i]); if (n != null) p[i] = +ucFmt(n * uc.a + uc.b, ucSig(p[i])); }
      // its number as imported, converted, with its bound as printed ("=<0.500")
      if (has) p[5] = typeof p[1] === "number" ? (read ? read.b : "") + ucFmt(p[1], read ? read.sig : ucSig(shown)) : read ? ucText(uc, shown) : shown;
      for (const k of ["lo", "hi"]) if (typeof p[k] === "number") p[k] = p[k] * uc.a + uc.b;
    }
  }
}
/* "95 mg/dL, ref 70–99" as reported, for a point of a converted series, with its report's range only ("≤ 100"; none
   for one whose range is from settings, #782); "" otherwise */
function ucOrig(s, p) {
  if (!s || !s.uc || !p || !p.orig) return "";
  const o = p.orig;
  return `${o.v}${o.own ? "" : " " + s.uc.from}${o.ref ? ", ref " + o.ref : ""}`;
}
/* the drawer's result rows ([name, value, unit, ref range, flag]) in the chosen units, by the conversion of the
   series with that unit and the row's series' name (d.series, #782: a report's "Glucose" is "Glucose, Blood"), else
   its own; each converted row keeps what was reported as row.orig = [value, unit, range] */
function applyUnitsToRows(labs, details) {
  const by = new Map();
  for (const s of labs) if (s.uc) by.set(s.name + "\u001f" + s.uc.from, s.uc);
  if (!by.size) return;
  for (const d of Object.values(details || {})) {
    if (!d || !Array.isArray(d.rows)) continue;
    for (const [i, row] of d.rows.entries()) {
      const uc = by.get((d.series?.[i] ?? row[0]) + "\u001f" + (row[2] || "")); if (!uc || row.orig) continue;
      if (!valueRead(row[1])) continue;   // text: nothing to convert (ranges.js valueRead, #874)
      const v = ucText(uc, row[1]);
      row.orig = [row[1], row[2], row[3]];
      const rr = row[3] ? ucRange(uc, row[3]) : row[3];
      row[1] = v; row[2] = uc.to;
      row[3] = rr == null ? `${row[3]} ${uc.from}` : rr;   // a range that can't be read stays, in its own unit
    }
  }
}
