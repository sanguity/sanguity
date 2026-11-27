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
/* a converted number, to as many significant figures as it was reported with (at least 3), however small (#898: at 6
   places at most, "1.2E-3 µmol/L" was "0.000014" mg/dL, 3% off its 0.0000136); a large one whole */
function ucFmt(x, sig) {
  if (!isFinite(x)) return "";
  if (x === 0) return "0";
  const dec = Math.max(0, Math.min(100, Math.max(3, sig) - 1 - Math.floor(Math.log10(Math.abs(x)))));
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
   a limit, or two numbers either side of "-", "–" or "to", each read by ranges.js valueRead (#874); with as, the low and
   high its import read in it, only where they're those (#886: "<0,500" left unread where a file's commas group
   thousands isn't read anew as 0.5) */
function ucRange(uc, text, as) {
  const f = r => ucFmt(r.x * uc.a + uc.b, r.sig), one = valueRead(text);
  if (one) return !as || as.filter(x => x != null).join() === `${one.x}` ? one.b + f(one) : null;
  const m = /^\s*(-?[^\s–-][^–-]*?)\s*(?:-|–|\bto\b)\s*(\S.*?)\s*$/i.exec(String(text ?? "")), a = m && valueRead(m[1]), b = m && valueRead(m[2]);
  return a && b && !a.c && !b.c && (!as || as[0] === a.x && as[1] === b.x) ? `${f(a)}–${f(b)}` : null;
}
/* a value reported with a unit of its own ("37 Cel", "175 cm", "72 /min": an import's vitals, which it puts in the
   series' unit), said as its number in the series' unit; where that isn't the number reported, what was is kept as
   p.said, which ucOrig says beside it (#1102: Labs said "37 Cel °F", the reported text with the series' unit after it) */
function saidApart(list) {
  for (const s of list) for (const p of s.pts || []) {
    const t = typeof p[5] === "string" ? p[5].trim() : "", m = /^(\S+)\s+([^\d\s-]\S*)$/.exec(t);
    const r = m && typeof p[1] === "number" && !valueRead(t) ? valueRead(m[1]) : null;
    if (!r) continue;
    if (r.x === p[1]) p[5] = m[1];
    else { p.said = `${m[1]} ${unitSaid(m[2])}`; p[5] = r.c ? r.b + fmtNum(p[1]) : ""; }   // its unit as people write it ("37 °C", #1103)
  }
}
/* convert series in place (lab series from DATA.labs, or watch series), each with its own conv */
function applyUnits(list) {
  for (const s of list) {
    const uc = s && !s.uc && ucChoose(s.conv);
    if (!uc) continue;
    s.uc = {...uc, from: s.unit};
    delete s.uk;   // its served unit's form isn't its unit's now: joined by the unit it's shown in (same.js joinSeries, #1102)
    s.unit = uc.to;
    for (const [j, p] of s.pts.entries()) {
      const has = p[5] != null && p[5] !== "", shown = has ? String(p[5]) : p[1] == null ? "" : fmtNum(p[1]), read = has ? valueRead(shown) : null;
      // a reported text with its own unit ("37 Cel", from an importer that converted it) is kept whole: text that isn't a
      // value as ranges.js valueRead reads one ("4,5", "=<5" and "> OR = 60" are, #874: they were said as their number)
      // its report's range said as Labs says one (ranges.js rangeWords, #782), not one from settings, which isn't the report's;
      // r, its report's low and high as its import read them (#886: its drawer row's, applyUnitsToRows), not another copy's
      p.orig = {v: p.said || shown, ref: p.rs === "settings" ? null : rangeWords(p), own: !!p.said || has && !read,
        r: p.rs === "settings" || s.rangeFrom?.[j] != null || p[2] == null && p[3] == null ? null : [p[2], p[3]]};
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
  if (s && !s.uc && p && p.said) return p.said;   // reported in another unit, its series not converted (saidApart)
  if (!s || !s.uc || !p || !p.orig) return "";
  const o = p.orig;
  return `${o.v}${o.own ? "" : " " + s.uc.from}${o.ref ? ", ref " + o.ref : ""}`;
}
/* the drawer's result rows' units as people write them (util.js unitSaid, #1103), after applyUnitsToRows: what's
   shown, and what was reported */
function rowUnitsSaid(details) {
  for (const d of Object.values(details || {})) if (d && Array.isArray(d.rows))
    for (const row of d.rows) if (Array.isArray(row)) { row[2] = unitSaid(row[2]); if (row.orig) row.orig[1] = unitSaid(row.orig[1]); }
}
/* the drawer's result rows ([name, value, unit, ref range, flag]) in the chosen units, each read only as its import
   read it (#886), as the page doesn't know a file's decimal mark: by its result (drawer.js rowPoint, its value as
   reported the row's) in that series' unit (d.series, #782: a report's "Glucose" is "Glucose, Blood"), its value that
   result's number as Labs says it, its range the low and high the import read in it (as printed where the page reads
   those, else as Labs words them), one it didn't read left in its own unit. A row with no such result is text to its
   import ("0,500" where a file's commas group thousands, kept as text, was converted as 0.5) and stays as reported.
   Each converted row keeps what was reported as row.orig = [value, unit, range] */
function applyUnitsToRows(labs, details) {
  if (!labs.some(s => s.uc)) return;
  for (const [id, d] of Object.entries(details || {})) {
    if (!d || !Array.isArray(d.rows)) continue;
    const ev = byId.get(+id) ?? byId.get(id);
    for (const [i, row] of d.rows.entries()) {
      const at = !row.orig && rowPoint(d, i, ev), uc = at && at.s.uc, p = at && at.p;
      if (!uc || unitSaid(uc.from) !== unitSaid(row[2] || "") || rowSaid(row[1]) !== rowSaid(p.orig.v)) continue;   // (either as written, #1103)
      row.orig = [row[1], row[2], row[3]];
      row[1] = p[5] || fmtNum(p[1]); row[2] = uc.to;
      if (row[3]) row[3] = p.orig.r ? ucRange(uc, row[3], p.orig.r) ?? rangeWords(p) : `${row[3]} ${uc.from}`;
    }
  }
}
