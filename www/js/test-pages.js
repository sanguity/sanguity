/* Sanguity · "What is this test?" (#163): beside a lab on Labs (its chart and the list), the heatmap, the Overview's
   tiles and the drawer's results, a link to a plain-language page about the test itself: what it measures and why
   it's done, never what a result means. The pages are a bundled, reviewed table (lab-test-pages.json, MedlinePlus's
   lab test pages first; server/lab_pages.py), read once (GET lab-test-pages); a lab is matched by its series' LOINC
   code, else its name. A lab with no page has no link.

   A link is the page's own address and nothing else: no value, date, profile or anything about the person goes with
   it. It opens in a new tab with no referrer (rel=noopener noreferrer), and its name says what it is, where it goes
   and that it opens a tab. The server contacts nothing; the browser opens the page only when the link is followed.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
let TEST_PAGES = null, TEST_PAGES_P = null;   // {pages: [{url, title, site}], codes: {code: n}, names: {name: n}}, or null
function testPagesLoad() {
  return TEST_PAGES_P = TEST_PAGES_P || api("lab-test-pages").then(r => r.ok ? r.json() : null).then(d => { TEST_PAGES = d; }).catch(() => {});
}
/* the page for a lab, by its name in the records (and its series' code), or null: a watch's measures have none */
function testPage(name) {
  if (!TEST_PAGES || !name) return null;
  const s = SERIES.find(x => x.name === name);
  if (s && s.watch) return null;
  const code = s ? String(s.loinc || (/^loinc:([\w-]+)/.exec(s.key || "") || [])[1] || "") : "";
  const low = String(name).trim().toLowerCase(), bare = low.replace(/\s*\([^()]*\)$/, "");   // "Glucose, Blood (mmol/L)"
  const n = [TEST_PAGES.codes[code], TEST_PAGES.names[low], TEST_PAGES.names[bare]].find(i => i != null);
  return n == null ? null : TEST_PAGES.pages[n] || null;
}
const testPageName = (name, p) => `What is this test? ${nice(name)}: “${p.title}” on ${p.site}, opens in a new tab`;
/* the link, or null. compact: a 24 px "?" (the list's rows, the tiles, the drawer), else the words */
function testLink(name, compact) {
  const p = testPage(name); if (!p) return null;
  const say = testPageName(name, p);
  return el("a", {class: "test-page" + (compact ? " tq" : ""), href: p.url, target: "_blank", rel: "noopener noreferrer", "aria-label": say, title: say,
    onclick: ev => ev.stopPropagation()},   // a drawer row's or a list row's own click stays its own
    compact ? el("span", {"aria-hidden": "true", text: "?"}) : ["What is this test?", el("span", {"aria-hidden": "true", class: "tp-ext", text: " ↗"})]);
}
