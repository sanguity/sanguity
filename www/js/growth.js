/* Sanguity · BMI over time (#195), and children's growth percentiles (#196, below). BMI from the weights tracked
   (smart scale and clinic visits) and About this person's height history (#194, person.js): each weight with the
   height in effect on its day. The number only; the WHO's
   adult categories only when the person turns them on (Body and activity), labelled as the WHO's; for someone under
   20 on a weight's day, never the adult categories.
     bmiOf(weight, unit, cm)     kg/m² from a weight in "kg" or "lb" and a height in cm, or null
     bmiRound(v)                 to one decimal, as it is shown (and as the WHO's categories are read)
     whoBmi(v)                   the WHO's adult category of a BMI: {range: "18.5–24.9", name: "normal weight"}
     bmiSeries(weights, unit, cfg)  [{t, v, w, h, age, child}] for weights [[t, value, ...]] in that unit: v the BMI,
                                 w the weight's point, h the height entry used, age in whole years on its day (null
                                 without a birth date), child: under 20 on its day. Left out: a weight with no height
                                 known by its day, one before 2 years of age, and, for a child, one more than
                                 BMI_FRESH days after the latest height (a child grows: a year-old height isn't theirs)
   server/person.py has bmi() and who_bmi() the same, for what the server shows (#225).
   Plain script; all files share one scope. Never used to judge: this shows what the numbers are. */
"use strict";
const LB_KG = 0.45359237;   // by definition
const BMI_ADULT = 20, BMI_FROM = 2, BMI_FRESH = 92;   // years, years, days
function bmiOf(weight, unit, cm) {
  if (typeof weight !== "number" || typeof cm !== "number" || !(weight > 0) || !(cm > 0)) return null;
  const kg = unit === "lb" ? weight * LB_KG : unit === "kg" ? weight : null;
  return kg == null ? null : kg / (cm / 100) ** 2;
}
const bmiRound = v => Math.round(v * 10) / 10;
/* the WHO's categories for adults (the WHO's names, said as the WHO's wherever they show) */
const WHO_BMI = [[-Infinity, 18.5, "under 18.5", "underweight"], [18.5, 25, "18.5–24.9", "normal weight"],
                 [25, 30, "25–29.9", "pre-obesity"], [30, Infinity, "30 and over", "obesity"]];
function whoBmi(v) {
  const r = bmiRound(v), c = WHO_BMI.find(c => r >= c[0] && r < c[1]);
  return c ? {range: c[2], name: c[3], lo: c[0], hi: c[1]} : null;
}
function bmiSeries(weights, unit, cfg) {
  const out = [], birth = personOf(cfg).birthDate;
  for (const w of weights) {
    const day = isoDay(w[0]), h = heightOn(day, cfg);
    if (!h) continue;
    const age = birth ? ageOn(birth, day) : null;
    if (age != null && age < BMI_FROM) continue;
    const child = age != null && age < BMI_ADULT;
    if (child && (dayTs(day) - dayTs(h.date)) / DAY > BMI_FRESH) continue;
    const v = bmiOf(w[1], unit, h.cm);
    if (v != null && isFinite(v)) out.push({t: w[0], v, w, h, age, child});
  }
  return out;
}

/* ---------------------------------------------------------------- children's growth percentiles (#196) */
/* Where a child's measurements fall on the published growth charts for age and sex: the WHO Child Growth Standards
   from birth to 2 years and the CDC's growth charts from 2 to 20 years, the convention the CDC recommends. The LMS
   tables are js/growth-lms.json (made by tools/growth_tables.py; docs/growth-charts.md says where each comes from and
   its licence), fetched only for a profile with a birth date and a sex used for reference ranges. Shows where a
   measurement falls, never what that means.
     growthTables()            a promise of the tables, fetched once (null if they can't be had)
     growthAge(day, cfg)       {days, chrono, corrected} on that day: days of age, corrected for a baby born early
                               (About this person's due date) before 2 years, or null (no birth date, before the birth or
                               the due date, or 20 and over)
     growthSet(days)           "who" under 2 years (730 days), "cdc" from 2 to 20
     growthPct(T, key, sex, days, x)  the percentile (0–100) of a measurement: key "weight" (kg), "height" (cm, lying
                               down under 2), "head" (cm, under 2) or "bmi" (from 2); sex "female" or "male"; null when
                               the charts have none for it
     growthCurve(T, key, sex, pct, from, to)  [[days, x]] of a percentile's curve between two ages in days
     pctText(p)                "62nd", "below the 1st", "above the 99th" */
const MONTH_DAYS = 30.4375, WHO_TO = 730, CDC_TO = 240 * MONTH_DAYS;
let GROWTH_T;   // the promise of the tables
function growthTables() {
  return GROWTH_T = GROWTH_T || fetch("js/growth-lms.json", {credentials: "same-origin"}).then(r => r.ok ? r.json() : null).catch(() => null);
}
/* the normal distribution's share below z (erfc to about 1e-7, Numerical Recipes' erfcc) */
function normCdf(z) {
  const x = Math.abs(z) / Math.SQRT2, t = 1 / (1 + 0.5 * x);
  const r = t * Math.exp(-x * x - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 + t * (0.27886807
    + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))));
  return z >= 0 ? 1 - r / 2 : r / 2;
}
const PCT_Z = {1: -2.3263479, 3: -1.8807936, 5: -1.6448536, 10: -1.2815516, 15: -1.0364334, 25: -0.6744898, 50: 0,
               75: 0.6744898, 85: 1.0364334, 90: 1.2815516, 95: 1.6448536, 97: 1.8807936, 99: 2.3263479};
const lmsZ = (x, L, M, S) => Math.abs(L) < 1e-12 ? Math.log(x / M) / S : ((x / M) ** L - 1) / (L * S);
const lmsX = (z, L, M, S) => Math.abs(L) < 1e-12 ? M * Math.exp(S * z) : M * (1 + L * S * z) ** (1 / L);
/* [L, M, S, ...] at an age, between the two rows around it (rows by age), or null outside them */
function lmsAt(rows, age) {
  if (!rows || !rows.length || age < rows[0][0] || age > rows[rows.length - 1][0]) return null;
  let lo = 0, hi = rows.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (rows[m][0] <= age) lo = m; else hi = m; }
  const a = rows[lo], b = rows[hi], f = b[0] === a[0] ? 0 : (age - a[0]) / (b[0] - a[0]);
  return a.slice(1).map((v, i) => v + (b[i + 1] - v) * f);
}
const growthSet = days => days <= WHO_TO ? "who" : "cdc";
const SEX_CODE = {male: "1", female: "2"};
/* the table rows and age for a measurement: [rows, age in the table's unit, set] or null */
function growthRows(T, key, sex, days) {
  if (!T || !SEX_CODE[sex] || !(days >= 0) || days > CDC_TO) return null;
  const set = growthSet(days), s = SEX_CODE[sex];
  const k = set === "who" ? {weight: "weight", height: "length", head: "head"}[key] : {weight: "weight", height: "stature", bmi: "bmi"}[key];
  if (!k) return null;
  return [T[set][k][s], set === "who" ? days : days / MONTH_DAYS, set, k];
}
function growthPct(T, key, sex, days, x) {
  const g = growthRows(T, key, sex, days);
  if (!g || !(x > 0)) return null;
  const [rows, age, set, k] = g, p = lmsAt(rows, age);
  if (!p) return null;
  const [L, M, S, sigma] = p;
  if (set === "cdc" && k === "bmi") {   // the CDC's 2022 extended BMI-for-age: above the 95th, by the distance from it
    const p95 = lmsX(PCT_Z[95], L, M, S);
    if (x >= p95) return 90 + 10 * normCdf((x - p95) / sigma);
  }
  let z = lmsZ(x, L, M, S);
  if (set === "who" && k !== "length" && Math.abs(z) > 3) {   // the WHO's: beyond ±3 SD in the distance between the 2 and 3 SD lines
    const s = z > 0 ? 1 : -1, sd3 = lmsX(3 * s, L, M, S), sd2 = lmsX(2 * s, L, M, S);
    z = 3 * s + (x - sd3) / Math.abs(sd3 - sd2);
  }
  return 100 * normCdf(z);
}
function growthCurve(T, key, sex, pct, from, to) {
  const out = [], z = PCT_Z[pct], step = Math.max(1, (to - from) / 120);
  for (let d = from; d <= to + 1e-9; d += step) {
    const g = growthRows(T, key, sex, d), p = g && lmsAt(g[0], g[1]);
    if (p) out.push([d, lmsX(z, p[0], p[1], p[2])]);
  }
  return out;
}
function growthAge(day, cfg) {
  const P = personOf(cfg), d = String(day || "").slice(0, 10);
  if (!P.birthDate || d < P.birthDate) return null;
  const chrono = Math.round((Date.parse(d + "T00:00:00Z") - Date.parse(P.birthDate + "T00:00:00Z")) / 864e5);
  if (ageOn(P.birthDate, d) >= 20) return null;
  const early = P.dueDate && P.dueDate > P.birthDate ? Math.round((Date.parse(P.dueDate + "T00:00:00Z") - Date.parse(P.birthDate + "T00:00:00Z")) / 864e5) : 0;
  if (early && chrono <= WHO_TO) {
    if (chrono < early) return null;   // before the due date: no chart has an age for it
    return {days: chrono - early, chrono, corrected: true};
  }
  return {days: chrono, chrono, corrected: false};
}
function pctText(p) {
  if (p == null) return "";
  if (p < 0.5) return "below the 1st";
  if (p >= 99.5) return "above the 99th";
  const n = Math.round(p), t = n % 100, u = n % 10;
  return n + (t >= 11 && t <= 13 ? "th" : u === 1 ? "st" : u === 2 ? "nd" : u === 3 ? "rd" : "th");
}
