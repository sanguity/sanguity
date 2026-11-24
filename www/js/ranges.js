/* Sanguity · a lab result's reference range and flag, worked out by one rule (#44, #752), and said one way: the person's
   own ranges, for results whose report gives none, and the flag of a result whose report gives a range and no flag.
   The same rules as server/ranges.py (the visit summary, its PDF and share links use that one, and the importers' flags;
   tests/test_ranges.py and tests/browser_check.py check they agree): a range from settings only for a numeric result
   with no low, no high and no flag on its report; the same lab (ignoring case and extra spaces) and the same unit
   (ignoring spaces; nothing converted); a row for the profile's sex wins over one for anyone; a row with an age band
   needs the birth date, by the age on the result's day. The birth date and sex are those in About this person (#194,
   person.js). Plain script; all files share one scope. */
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
/* a value given as a bound ("<15", "≤ 0.5", ">90"): its comparator, "<", "<=", ">" or ">=", else "" (#718;
   server/ranges.py comparator) */
const RANGE_CMP = {"=<": "<=", "≤": "<=", "=>": ">=", "≥": ">="};
const rangeCmp = t => { const m = /^\s*(<=|>=|=<|=>|<|>|≤|≥)/.exec(t ?? ""); return m ? RANGE_CMP[m[1]] || m[1] : ""; };
/* the one flag rule (#752; server/ranges.py flag, which the importers use too): "H" above the high, "L" below the low,
   whatever value a bound (its comparator c) stands for, else "": "<15" against a low of 15 is low (every value under 15
   is), ">90" against a high of 90 high; "<15" against a low of 10 is neither (it may be 12 or 9). e, a point's edge
   (rangeEdge): "<" where its high leaves its edge out, ">" its low (#780): 5 against "<5" is high, against "<=5" not */
const rangeFlag = (v, lo, hi, c = "", e = "") => hi != null && c[0] !== "<" && (c === ">" || e.includes("<") ? v >= hi : v > hi) ? "H"
  : lo != null && c[0] !== ">" && (c === "<" || e.includes(">") ? v <= lo : v < lo) ? "L" : "";
/* a point's edge (p[7], server/importers/common.py range_edge): "<" where its one-sided range was printed "<5", leaving
   5 out of it, ">" for ">60"; "" for one that keeps it ("<=5", ">=60") or a range of two limits (#780) */
const rangeEdge = p => String(p[7] || "");
/* each lab result's range (p[2], p[3]), flag (p[4]) and its range's source (p.rs "settings", else its report): the
   report's own range and flag where it gives them; where it gives a range and no flag, the flag rangeFlag works out
   against it; where it gives neither, a range from settings that fits, p.rs = "settings", its flag worked out against
   that. Every page reads a result's flag from here (Labs, the heatmap, the Overview, Compare, the lab panel).
   -> how many results took a range from settings */
function applyRangeOverrides(series, cfg) {
  let n = 0;
  for (const s of series) {
    if (s.watch) continue;
    for (const p of s.pts) {
      if (typeof p[1] !== "number" || !isFinite(p[1]) || String(p[4] || "").trim()) continue;
      if (p[2] == null && p[3] == null) {
        const r = rangeRowFor(cfg, s.name, s.unit, String(p[0] || "").slice(0, 10));
        if (!r) continue;
        p[2] = r.low ?? null; p[3] = r.high ?? null; p.rs = "settings"; n++;
        if (p[7]) p[7] = "";   // (a range from settings keeps both its edges)
      }
      p[4] = rangeFlag(p[1], p[2], p[3], rangeCmp(p[5]), rangeEdge(p));
    }
  }
  return n;
}
let RANGES_USED = 0;   // how many results took a range from settings (app.js)
const RANGE_NOTE_SETTINGS = " Results whose report gives no range are coloured against a range you set in Settings, marked “range from settings”.";
/* ---- each result against its own range on a chart (#58): never one range across results or labs.
   A result's range (p[2], p[3]) is already in the units shown (units.js converts it with the value, #41), so ranges
   from two labs are compared after conversion. rangeWords(p): "0.7–1.3", "≤ 5" or "≥ 60" (null: it has none), or
   "< 5" and "> 60" where its limit leaves its edge out (rangeEdge, #780).
   rangeSteps(pts, from, to): the reference band as steps in time, [{a, b, lo, hi, key (its words), rs: "report" or
   "settings"}]: each result with a range its step, from halfway after the result before it to halfway to the next (from
   and to at the ends); a result with none has none, so no band is drawn beside a result whose report gave no range
   (#752: a report's 3.5–5.1 ran on under the 3.4 and 5.3 after it, which had none); neighbours with the same range from
   the same source are one step ("< 5" and "≤ 5" two, #780), so a series whose results all agree is one band (a
   report's range and the same one from settings are two, each said as what it is, #649). server/ranges.py steps() is
   the same rule, for the PDF.
   rangesDiffer(pts): the different ranges of these results, in order ("0.7–1.3 and 0.6–1.2"), or null if they agree. */
const rangeWords = p => p[2] != null && p[3] != null ? `${p[2]}–${p[3]}` : p[3] != null ? `${rangeEdge(p).includes("<") ? "<" : "≤"} ${p[3]}`
  : p[2] != null ? `${rangeEdge(p).includes(">") ? ">" : "≥"} ${p[2]}` : null;
const rangeKey = rangeWords;
function rangeSteps(pts, from, to) {
  const out = [];
  pts.forEach((p, i) => {
    const key = rangeKey(p); if (key == null) return;
    const a = i ? (pts[i - 1].ts + p.ts) / 2 : from, b = i < pts.length - 1 ? (p.ts + pts[i + 1].ts) / 2 : to, last = out.at(-1);
    const rs = p.rs === "settings" ? "settings" : "report";
    if (last && last.key === key && last.rs === rs && last.b === a) last.b = b; else out.push({a, b, lo: p[2], hi: p[3], key, rs});
  });
  return out;
}
function rangesDiffer(pts) {
  const w = [...new Set(pts.map(rangeWords).filter(Boolean))];
  if (w.length < 2) return null;
  return w.length > 4 ? w.slice(0, 3).join(", ") + ` and ${w.length - 3} more` : w.slice(0, -1).join(", ") + " and " + w[w.length - 1];
}
const RANGE_DIFFERS = "Reference range differs between results";
/* ---- said one way (#752), in every cell, band label and tooltip: a result's range, with where it came from.
   refWords(p): "13.5–17.5", or "70–99 (range from settings)"; refText(p): "ref " and those (null for no range).
   rangeSrc(st): a band step's source in words, "on report" or "from settings"; rangeSaid(st): its range and that,
   "3.5–5.1 on report", "5–20 from settings" */
const refWords = p => rangeWords(p) && rangeWords(p) + (p.rs === "settings" ? ` (${RANGE_FROM_SETTINGS})` : "");
const refText = p => rangeWords(p) && "ref " + refWords(p);
const rangeSrc = st => st.rs === "settings" ? "from settings" : "on report";
const rangeSaid = st => `${st.key} ${rangeSrc(st)}`;
/* ---- flagged results, said one way (#819): a ▲ or ▼ is outside its range; a ◆, a report's flag that says neither way
   (an "A"), is flagged on its report, its value inside its range or with none, and is "Flagged" everywhere (charts.js
   FLAG_OF, the summary and share links: summary.out_of_range). Every heading, count and note of flagged results says
   them by flaggedWords(flags, form): flags, each result's (p[4], or the summary's "High", "Low", "Flagged"); form "its"
   for one result each ("outside its reference range": the Overview's list, the lab panel), "count" for a number of
   them ("7 outside reference range": Labs' chart, a share link's and the summary's charts, the heatmap), "short" for a
   badge, a fact or a heading ("2 out of range"), "head" for a narrow column's heading ("Outside": the heatmap's); each
   with what a ◆ is where there's one ("… or flagged on its report", "… or flagged on their report", "… or flagged").
   server/ranges.py flagged_words is the same, for the summary */
const FLAGGED_FORMS = {its: ["outside its reference range", " or flagged on its report"], count: ["outside reference range", " or flagged on their report"],
  short: ["out of range", " or flagged"], head: ["Outside", " or flagged"]};
const flagSaysNeither = f => !!String(f ?? "").trim() && !/^\s*[hl]/i.test(f);   // a ◆ (FLAG_OF)
const flaggedWords = (flags, form = "count") => FLAGGED_FORMS[form][0] + ([...flags].some(flagSaysNeither) ? FLAGGED_FORMS[form][1] : "");
const capFirst = t => t.charAt(0).toUpperCase() + t.slice(1);
