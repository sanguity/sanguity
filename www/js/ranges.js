/* Sanguity · reference ranges from settings (#44): the person's own ranges, for results whose report gives none.
   The same rules as server/ranges.py (the visit summary PDF uses that one; tests/browser_check.py checks they agree):
   only a numeric result with no low, no high and no flag on its report; the same lab (ignoring case and extra spaces)
   and the same unit (ignoring spaces; nothing converted); a row for the profile's sex wins over one for anyone; a row
   with an age band needs the birth date, by the age on the result's day. Plain script; all files share one scope. */
"use strict";
const RANGE_FROM_SETTINGS = "range from settings";
const rNorm = n => String(n || "").split(/\s+/).filter(Boolean).join(" ").toLowerCase();
const rUnit = u => String(u || "").replace(/\s+/g, "");
function ageOn(birth, day) {
  const b = /^(\d{4})-(\d{2})-(\d{2})/.exec(birth || ""), d = /^(\d{4})-(\d{2})-(\d{2})/.exec(day || "");
  if (!b || !d || d[0] < b[0]) return null;
  return +d[1] - +b[1] - ((+d[2] * 100 + +d[3]) < (+b[2] * 100 + +b[3]) ? 1 : 0);
}
function rangeRowFor(cfg, lab, unit, day) {
  const rows = (cfg && Array.isArray(cfg.overrides)) ? cfg.overrides : [];
  if (!rows.length) return null;
  const sex = cfg.sex || "", age = ageOn(cfg.birthDate, day), n = rNorm(lab), u = rUnit(unit);
  let best = null;
  for (const r of rows) {
    if (rNorm(r.lab) !== n || rUnit(r.unit) !== u) continue;
    const rs = r.sex || "any";
    if (rs !== "any" && rs !== sex) continue;
    const a = r.ageFrom ?? null, b = r.ageTo ?? null;
    if ((a != null || b != null) && (age == null || (a != null && age < a) || (b != null && age > b))) continue;
    if (!best || ((best.sex || "any") === "any" && rs !== "any")) best = r;
  }
  return best;
}
/* fill in the range (p[2], p[3]) and flag (p[4]) of each lab result that has none, marking it p.rs = "settings";
   returns how many results took a range from settings */
function applyRangeOverrides(series, cfg) {
  let n = 0;
  if (!cfg || !Array.isArray(cfg.overrides) || !cfg.overrides.length) return n;
  for (const s of series) {
    if (s.watch) continue;
    for (const p of s.pts) {
      if (typeof p[1] !== "number" || !isFinite(p[1]) || p[2] != null || p[3] != null || String(p[4] || "").trim()) continue;
      const r = rangeRowFor(cfg, s.name, s.unit, String(p[0] || "").slice(0, 10));
      if (!r) continue;
      p[2] = r.low ?? null; p[3] = r.high ?? null;
      p[4] = r.high != null && p[1] > r.high ? "H" : r.low != null && p[1] < r.low ? "L" : "";
      p.rs = "settings"; n++;
    }
  }
  return n;
}
let RANGES_USED = 0;   // how many results took a range from settings (app.js)
const RANGE_NOTE_SETTINGS = " Results whose report gives no range are coloured against a range you set in Settings, marked “range from settings”.";
/* "ref 70–99" for a result, said as from settings when it is */
const refText = p => `ref ${p[2] ?? ""}–${p[3] ?? ""}` + (p.rs === "settings" ? ` (${RANGE_FROM_SETTINGS})` : "");
