/* Sanguity · reference ranges from settings (#44): the person's own ranges, for results whose report gives none.
   The same rules as server/ranges.py (the visit summary PDF uses that one; tests/browser_check.py checks they agree):
   only a numeric result with no low, no high and no flag on its report; the same lab (ignoring case and extra spaces)
   and the same unit (ignoring spaces; nothing converted); a row for the profile's sex wins over one for anyone; a row
   with an age band needs the birth date, by the age on the result's day. The birth date and sex are those in About this
   person (#194, person.js). Plain script; all files share one scope. */
"use strict";
const RANGE_FROM_SETTINGS = "range from settings";
const rNorm = n => String(n || "").split(/\s+/).filter(Boolean).join(" ").toLowerCase();
const rUnit = u => String(u || "").replace(/\s+/g, "");
/* ageOn (whole years on a day) is person.js's, loaded before this. rangeConfig(cfg): what rangeRowFor takes, from the
   settings: the ranges set, and the birth date and sex in About this person (#194; server/ranges.py config()) */
const rangeConfig = cfg => ({birthDate: cfg?.person?.birthDate || "", sex: cfg?.person?.sex || "", overrides: cfg?.ranges?.overrides || []});
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
/* ---- each result against its own range on a chart (#58): never one range across results or labs.
   A result's range (p[2], p[3]) is already in the units shown (units.js converts it with the value, #41), so ranges
   from two labs are compared after conversion. rangeWords(p): "0.7–1.3", "≤ 5" or "≥ 60" (null: it has none).
   rangeSteps(pts, from, to): the reference band as steps in time, [{a, b, lo, hi, key (its words), rs}], each result's range from halfway
   after the result before it to halfway to the next (from and to at the ends); results with no range take no part,
   and neighbours with the same range are one step, so a series whose results all agree is one band.
   rangesDiffer(pts): the different ranges of these results, in order ("0.7–1.3 and 0.6–1.2"), or null if they agree. */
const rangeWords = p => p[2] != null && p[3] != null ? `${p[2]}–${p[3]}` : p[3] != null ? `≤ ${p[3]}` : p[2] != null ? `≥ ${p[2]}` : null;
const rangeKey = rangeWords;
function rangeSteps(pts, from, to) {
  const rp = pts.filter(p => rangeKey(p) != null), out = [];
  rp.forEach((p, i) => {
    const a = i ? (rp[i - 1].ts + p.ts) / 2 : from, b = i < rp.length - 1 ? (p.ts + rp[i + 1].ts) / 2 : to, last = out[out.length - 1];
    if (last && last.key === rangeKey(p)) last.b = b; else out.push({a, b, lo: p[2], hi: p[3], key: rangeKey(p), rs: p.rs});
  });
  return out;
}
function rangesDiffer(pts) {
  const w = [...new Set(pts.map(rangeWords).filter(Boolean))];
  if (w.length < 2) return null;
  return w.length > 4 ? w.slice(0, 3).join(", ") + ` and ${w.length - 3} more` : w.slice(0, -1).join(", ") + " and " + w[w.length - 1];
}
const RANGE_DIFFERS = "Reference range differs between results";
