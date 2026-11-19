/* Sanguity · About this person (#194): the profile's birth date, the sex used for reference ranges, and height with a
   history (server/person.py keeps them, as the settings person.birthDate, person.sex and person.heights). The records
   page has them in CFG.person, with every setting; Settings → About this person (about.js, #267) shows and changes them.

   For what is built on them (BMI #195, growth percentiles #196, heart-rate zones #197), the same as server/person.py:
     personOf(cfg)               {birthDate, sex, heights, dueDate} from the merged settings, filled in ("" and [] when
                                 not set); dueDate: for a baby born early, the day they were due (#196, corrected age)
     ageOn(birth, day)           whole years on that day (both "YYYY-MM-DD..."), or null (no birth date, or before it)
     ageMonthsOn(birth, day)     whole months on that day, or null: a child's age, for growth charts
     personAgeOn(day, cfg)       ageOn with this profile's birth date; personAgeMonthsOn(day, cfg) the same in months
     rangeSexOf(cfg)             "female", "male" or "" (not set): what labs base their reference ranges on, never
                                 gender identity
     heightOn(day, cfg)          the height in effect on that day: the latest entry dated on or before it,
                                 {date, cm, unit}, or null (none known by then). cm is the one unit heights are kept in;
                                 unit is how it was typed ("cm" or "ftin")
     heightText(cm, unit)        "172.5 cm" or "5 ft 8 in"; heightUnit(pref, typed) the unit to show it in: the
                                 person's Units choice (CFG.units.height: "cm" or "ftin"), or as it was typed ("reported")
   cfg defaults to CFG where the page has one. Never use these to judge a result or replace a report's own range.
   Plain script; all files share one scope. */
"use strict";
const PERSON_SEXES = {"": "Not set", female: "Female", male: "Male"};
const IN_CM = 2.54;
const _pcfg = cfg => cfg !== undefined ? cfg : (typeof CFG !== "undefined" ? CFG : null);
function personOf(cfg) {
  const p = (_pcfg(cfg) || {}).person || {};
  return {birthDate: p.birthDate || "", sex: p.sex || "", heights: Array.isArray(p.heights) ? p.heights : [], dueDate: p.dueDate || ""};
}
const _ymd = s => /^(\d{4})-(\d{2})-(\d{2})/.exec(s || "");
function ageOn(birth, day) {
  const b = _ymd(birth), d = _ymd(day);
  if (!b || !d || d[0] < b[0]) return null;
  return +d[1] - +b[1] - ((+d[2] * 100 + +d[3]) < (+b[2] * 100 + +b[3]) ? 1 : 0);
}
/* a month is complete on the same day of the month (or on its last day, for a birth on the 29th to 31st) */
function ageMonthsOn(birth, day) {
  const b = _ymd(birth), d = _ymd(day);
  if (!b || !d || d[0] < b[0]) return null;
  const last = new Date(Date.UTC(+d[1], +d[2], 0)).getUTCDate();
  return (+d[1] - +b[1]) * 12 + (+d[2] - +b[2]) - (+d[3] < Math.min(+b[3], last) ? 1 : 0);
}
const personAgeOn = (day, cfg) => ageOn(personOf(cfg).birthDate, day);
const personAgeMonthsOn = (day, cfg) => ageMonthsOn(personOf(cfg).birthDate, day);
const rangeSexOf = cfg => personOf(cfg).sex;
function heightOn(day, cfg) {
  const d = String(day || "").slice(0, 10);
  let best = null;
  for (const h of personOf(cfg).heights) if (h.date <= d && (!best || h.date > best.date)) best = h;
  return best;
}
/* [feet, inches to one decimal] */
function heightFtIn(cm) {
  const inch = Math.round(cm / IN_CM * 10) / 10, ft = Math.floor(inch / 12);
  return [ft, Math.round((inch - ft * 12) * 10) / 10];
}
function heightText(cm, unit) {
  if (unit === "ftin") { const [ft, i] = heightFtIn(cm); return `${ft} ft ${i} in`; }
  return `${Math.round(cm * 10) / 10} cm`;
}
const heightUnit = (pref, typed) => pref === "cm" || pref === "ftin" ? pref : (typed === "ftin" ? "ftin" : "cm");
