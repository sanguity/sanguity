/* Sanguity · dates in each person's own time zone and date format (#42): every page that shows or splits dates
   uses these, never the browser's zone directly. Plain script, loaded right after util.js (uses api).

   The two settings are per user (server/settings.py, "dates.timeZone" and "dates.format"), read with
   datesReady(); the first page loaded after someone first signs in sets them from this browser. Until they're
   read (or if that fails), the browser's own zone and language are used.

   fmtD / fmtDT / fmtMonth   an instant (ms) as a date, a date and time, a month
   recordDate(s)             a date or date and time a record gives as text, "2026-11-02 08:10", the same way
   isoDay(t)                 the instant's day, "YYYY-MM-DD", where this person's day begins at their midnight
   dayTs("YYYY-MM-DD")       noon of that day in their zone (a date without a time, as a point on a chart)
   zParse(s)                 an ISO time string to ms; one without an offset is read as their wall-clock time
   zdate(t)                  a Date-like object (getDate, setHours, setMonth, toLocaleDateString...) whose calendar
                             is their zone's: for stepping through days, weeks and months on axes and in bins
   zToday()                  today's "YYYY-MM-DD" for them */
"use strict";
const DATES = {zone: "", format: ""};   // "" until read: the browser's zone; the browser language's own format
const browserZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch (e) { return "UTC"; } };
/* the browser language's order of day, month and year, as a format setting */
function browserFormat() {
  try {
    const order = new Intl.DateTimeFormat(undefined, {year: "numeric", month: "short", day: "numeric"}).formatToParts(new Date(2026, 10, 15))
      .map(p => p.type).filter(t => t === "year" || t === "month" || t === "day").join(",");
    return order.startsWith("year") ? "ymd" : order.startsWith("day") ? "dmy" : "mdy";
  } catch (e) { return "mdy"; }
}
const DATE_FORMATS = {mdy: "Nov 15, 2026", dmy: "15 Nov 2026", ymd: "2026-11-15"};

let _zfmt = null, _zcache = new Map();
function useDates(zone, format) {
  let z = "";
  if (zone) { try { new Intl.DateTimeFormat("en-US", {timeZone: zone}); z = zone; } catch (e) {} }   // an unknown zone: the browser's
  DATES.zone = z; DATES.format = DATE_FORMATS[format] ? format : "";
  _zcache = new Map();
  _zfmt = z ? new Intl.DateTimeFormat("en-US", {timeZone: z, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", second: "numeric"}) : null;
}
const _withZone = (o = {}) => DATES.zone && !o.timeZone ? {...o, timeZone: DATES.zone} : o;
/* t in English as the app writes it everywhere, the server's PDFs too (summary.py fmt_date): en-US month names and
   clock ("Sep 6", "2:05 PM"; #543: the browser's region gave "Sept" and "14:05" outside the US), a day and a month
   in the order of this person's format ("6 Sep" for dmy), with these options (which name the parts to show: year,
   month, hour...), in their zone: what Date's toLocaleDateString, toLocaleTimeString and toLocaleString give, with
   each formatter made once and kept, since making one is most of the time those take (a ten-year record shows
   thousands of dates, #201). A time keeps its AM or PM here, at the source (#517): the space between them is a
   no-break one, so no line on any page ends "10:06" with "AM" on the next */
let _bf = "";
const _order = () => DATES.format || (_bf ||= browserFormat());   // the format chosen, else the browser language's order
const _fmts = new Map();
function _fmt(t, o) {
  const key = JSON.stringify(o) + DATES.zone;
  let f = _fmts.get(key);
  if (!f) _fmts.set(key, f = new Intl.DateTimeFormat("en-US", _withZone(o)));
  const d = new Date(t);
  if (isNaN(d)) return "Invalid Date";   // as Date's own say it, where format() would throw
  if (!o.hour) {
    if (!o.day || !/^(short|long)$/.test(o.month) || _order() !== "dmy") return f.format(d);
    const p = {};
    for (const x of f.formatToParts(d)) p[x.type] = x.value;
    return [p.weekday, p.day, p.month, p.year].filter(Boolean).join(" ");
  }
  const p = f.formatToParts(d), am = i => p[i]?.type === "dayPeriod";
  return p.map((x, i) => x.type === "literal" && (am(i - 1) || am(i + 1)) ? x.value.replace(/\s/g, "\u00a0") : x.value).join("");
}
/* ms to add to an instant to get their wall-clock time as if it were UTC (offsets change only on a quarter hour) */
function zOffset(t) {
  if (!_zfmt) return -new Date(t).getTimezoneOffset() * 6e4;
  const k = Math.floor(t / 9e5);
  let o = _zcache.get(k);
  if (o === undefined) {
    const b = k * 9e5, p = {};
    for (const x of _zfmt.formatToParts(new Date(b))) p[x.type] = x.value;
    o = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute, +p.second) - b;
    if (_zcache.size > 2e5) _zcache.clear();
    _zcache.set(k, o);
  }
  return o;
}
/* the instant a wall-clock time (as UTC ms) names in their zone; in a gap (spring forward), just after it */
const zFromWall = w => w - zOffset(w - zOffset(w));

class ZDate {
  constructor(t) { this.t = t === undefined ? Date.now() : +t; this._sync(); }
  _sync() { this.w = new Date(this.t + zOffset(this.t)); }
  _set(f, a) { this.w[f](...a); this.t = zFromWall(this.w.getTime()); this._sync(); return this.t; }
  getTime() { return this.t; } valueOf() { return this.t; }
  getFullYear() { return this.w.getUTCFullYear(); } getMonth() { return this.w.getUTCMonth(); } getDate() { return this.w.getUTCDate(); }
  getDay() { return this.w.getUTCDay(); } getHours() { return this.w.getUTCHours(); } getMinutes() { return this.w.getUTCMinutes(); }
  setFullYear(...a) { return this._set("setUTCFullYear", a); } setMonth(...a) { return this._set("setUTCMonth", a); }
  setDate(...a) { return this._set("setUTCDate", a); } setHours(...a) { return this._set("setUTCHours", a); }
  toLocaleDateString(loc, o) { return loc === undefined && o && (o.year || o.month || o.day || o.weekday) ? _fmt(this.t, o) : new Date(this.t).toLocaleDateString(loc, _withZone(o)); }
  toLocaleString(loc, o) { return new Date(this.t).toLocaleString(loc, _withZone(o)); }
  toLocaleTimeString(loc, o) { return new Date(this.t).toLocaleTimeString(loc, _withZone(o)); }
  iso() { return this.w.toISOString().slice(0, 10); }
}
const zdate = t => new ZDate(t);
const isoDay = t => zdate(t).iso();
const zToday = () => isoDay(Date.now());
const dayTs = d => zFromWall(Date.parse(String(d).slice(0, 10) + "T12:00:00Z"));
/* an ISO time: with Z or an offset it's an instant; a bare date is noon that day; without an offset it's their wall clock */
function zParse(s) {
  if (s == null || s === "") return NaN;
  if (typeof s === "number") return s;
  s = String(s);
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return dayTs(s);
  if (/(Z|[+-]\d{2}:?\d{2})$/i.test(s)) return Date.parse(s);
  const w = Date.parse(s + "Z");
  return isNaN(w) ? Date.parse(s) : zFromWall(w);
}

/* ---------------------------------------------------------------- formatting: each takes ms, a Date or an ISO string */
const _ms = t => typeof t === "string" ? zParse(t) : +t;
function _ymdParts(t) {
  const z = zdate(t);
  return {y: z.getFullYear(), m: z.getMonth(), d: z.getDate(),
          mon: _fmt(t, {month: "short"})};
}
function fmtD(t) {
  t = _ms(t);
  if (!isFinite(t)) return "";
  const f = _order(), p = _ymdParts(t);
  if (f === "ymd") return `${p.y}-${String(p.m + 1).padStart(2, "0")}-${String(p.d).padStart(2, "0")}`;
  return f === "dmy" ? `${p.d} ${p.mon} ${p.y}` : `${p.mon} ${p.d}, ${p.y}`;
}
const fmtTime = t => _fmt(_ms(t), {hour: "numeric", minute: "2-digit"});
function fmtDT(t) {
  t = _ms(t);
  if (!isFinite(t)) return "";
  return `${fmtD(t)}${_order() === "ymd" ? " " : ", "}${fmtTime(t)}`;
}
const fmtMonth = t => _fmt(_ms(t), {year: "numeric", month: "long"});
/* seconds since 1970 (what the API sends for when things happened), as a date and time */
const fmtWhen = s => s ? fmtDT(s * 1000) : "—";
/* a date a record gives as text, "2026-11-02" or "2026-11-02 08:10", in this person's date format (#248, #311);
   a time without a zone is the wall clock the record says. Anything else (a portal's own wording) as the record has it */
function recordDate(s) {
  if (typeof s !== "string") return s;
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return fmtD(dayTs(s));
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$/.test(s)) return fmtDT(zParse(s.replace(" ", "T")));
  return s;
}

/* ---------------------------------------------------------------- the settings */
let _datesP = null;
/* Read this person's time zone and date format (once per page); the first time, set them from this browser.
   Resolves either way: on failure the browser's own are used. */
function datesReady() {
  return _datesP || (_datesP = (async () => {
    try {
      const r = await api("settings/user");
      if (!r.ok) return;
      const u = await r.json();
      const want = {"dates.timeZone": browserZone(), "dates.format": browserFormat()};
      for (const [k, v] of Object.entries(want)) {
        if (u[k]) continue;   // chosen already
        // only if none was chosen meanwhile (another tab), and not logged as a change: nobody made one (#524)
        const w = await api(`settings/user/${encodeURIComponent(k)}`, {method: "PUT", body: JSON.stringify({value: v, if_unset: true})});
        if (w.ok) u[k] = (await w.json())[k];
      }
      useDates(u["dates.timeZone"], u["dates.format"]);
    } catch (e) {}
  })());
}
