/* Sanguity · BMI over time (#195), from the weights tracked (smart scale and clinic visits) and About this person's
   height history (#194, person.js): each weight with the height in effect on its day. The number only; the WHO's
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
