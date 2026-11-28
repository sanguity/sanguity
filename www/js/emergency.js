/* Sanguity · the emergency card (#1126), on Sharing → Emergency card: what's on it, as a preview, then the two files
   to make from it, each a file the person keeps (no address opens it, no share link carries it):
   - the printable card, a PDF the server makes (server/emergency.py), on Letter or A4 (the browser's region chooses
     at first);
   - the lock-screen image, a PNG drawn here on a canvas from the same card, sized for this screen and upright: the
     name, allergies with their reactions, blood type and contacts, and "Medications and conditions: see the card in
     my wallet" only with "I carry the printed card" on (off each time). A canvas's PNG holds the picture and nothing
     else: no text, no time, no place.
   drawEmergency(p) draws it for p (sharing.js, when the group is shown or the profile changes); call() and api() are
   the page's. Nothing here is put into innerHTML. Plain script; all files share one scope. */
"use strict";
let EC_P = null;
const EC_LETTER = /-(US|CA|MX|PH|CL|CO|VE|GT|CR|PA|DO|PR|SV|NI|HN|BZ)$/i;   // regions that print on Letter
async function drawEmergency(p) {
  EC_P = p;
  sayIn($("#ecMsg"), "");
  let c;
  try { c = await call(`profiles/${p.id}/emergency`); }
  catch (e) { if (EC_P === p) { $("#ecBody").replaceChildren(); sayIn($("#ecMsg"), e.message, true); } return; }
  if (EC_P !== p) return;
  const wallet = el("input", {type: "checkbox", role: "switch", id: "ecWallet"});
  const paper = el("select", {id: "ecPaper"}, el("option", {value: "letter", text: "Letter"}), el("option", {value: "a4", text: "A4"}));
  paper.value = EC_LETTER.test(navigator.language || "") ? "letter" : "a4";
  const img = el("img", {id: "ecImg", class: "ec-img", alt: ""});
  const showImg = () => { const cv = ecCanvas(c, wallet.checked); img.src = cv.toDataURL("image/png");
    img.alt = "Preview of the lock-screen image: " + cv.lines.join(". "); };   // (what it shows, cut short or not)
  wallet.addEventListener("change", showImg);
  $("#ecBody").replaceChildren(
    el("p", {class: "hint", text: `What's on it, as of ${ecDay(c.asOf)}. `},
      el("a", {href: `settings.html?profile=${p.id}#about`, text: "Blood type and emergency contacts"}), " are in Settings, under About this person."),
    el("dl", {class: "about-dl", id: "ecPreview"}, ...c.sections.flatMap(s => [el("dt", {text: s.heading}),
      el("dd", {}, ...s.lines.map(t => el("div", {text: t})))])),
    setRow([el("label", {class: "set-row-name", for: "ecPaper", text: "Printable card (PDF)"}),
      el("p", {class: "hint", text: "One page: all of the above in large type, and a wallet-size card to cut out and fold."})],
      el("div", {class: "row"}, paper, el("button", {class: "btn primary", type: "button", id: "ecPdf", text: "Download PDF", onclick: ev => ecPdf(p, paper.value, ev.currentTarget)}))),
    setRow([el("p", {class: "set-row-name", text: "Lock-screen image (PNG)"}),
      el("p", {class: "hint", text: "Sized for this screen, to set as your phone's lock screen: the name, allergies, blood type and emergency contacts."})],
      [el("label", {class: "check switch"}, wallet, "I carry the printed card"),   // (check: a row in .acct, as a switch is elsewhere)
        el("p", {class: "hint", text: "Adds “Medications and conditions: see the card in my wallet” to the image."}),
        img, el("div", {class: "row"}, el("button", {class: "btn", type: "button", id: "ecPng", text: "Download image", onclick: ev => ecPng(p, wallet.checked, ev.currentTarget)}))]));
  showImg();
  $("#emergency").dataset.profile = p.id;   // whose card is drawn (the browser check waits for it)
}
const ecDay = d => fmtD(dayTs(d));

/* a file saved from memory, as the visit summary's (summary.js) */
function ecSave(blob, name) {
  const url = URL.createObjectURL(blob), a = el("a", {href: url, download: name, hidden: true});
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  sayIn($("#ecMsg"), `Downloaded ${name}.`);
}
/* not disabled while it's made (aria-disabled): focus stays on the button */
async function ecBusy(b, said, work) {
  if (b.getAttribute("aria-disabled") === "true") return;
  b.setAttribute("aria-disabled", "true"); sayIn($("#ecMsg"), said);
  try { await work(); } catch (e) { sayIn($("#ecMsg"), e.message, true); } finally { b.removeAttribute("aria-disabled"); }
}
const ecPdf = (p, paper, b) => ecBusy(b, "Making the PDF…", async () => {
  const r = await api(`profiles/${p.id}/emergency/pdf`, {method: "POST", body: JSON.stringify({paper})});
  if (r.status === 401) { location.replace("login.html"); return; }
  if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error((typeof j.detail === "string" && j.detail) || `Not made (${r.status}).`); }
  ecSave(await r.blob(), (/filename="([^"]+)"/.exec(r.headers.get("content-disposition") || "") || [])[1] || "emergency-card.pdf");
});
const ecPng = (p, wallet, b) => ecBusy(b, "Making the image…", async () => {
  const c = await call(`profiles/${p.id}/emergency/image`, "POST", {wallet});   // the card as it is now (and logged)
  const blob = await new Promise(ok => ecCanvas(c, wallet).toBlob(ok, "image/png"));
  if (!blob) throw new Error("This browser couldn't make the image.");
  ecSave(blob, c.filename);
});

/* ---------------------------------------------------------------- the lock-screen image */
/* its lines, top to bottom: [{t, k}], k the kind ("title", "name", "head", "line", "note", "date"). keep: how many of
   a part's lines are shown ({Allergies: n, "Emergency contacts": n}; all, if not said), the rest said as "and N more" */
const EC_MORE = {"Allergies": ["allergy", "allergies"], "Emergency contacts": ["contact", "contacts"]};
function ecImageLines(c, wallet, keep = {}) {
  const out = [{t: "EMERGENCY INFORMATION", k: "title"}, {t: c.profile, k: "name"}];
  for (const s of c.short) {
    const n = Math.min(keep[s.heading] ?? s.lines.length, s.lines.length), more = s.lines.length - n;
    out.push({t: s.heading.toUpperCase(), k: "head"}, ...s.lines.slice(0, n).map(t => ({t, k: "line"})));
    if (more) out.push({t: `and ${more} more ${(EC_MORE[s.heading] || ["", ""])[more === 1 ? 0 : 1]}`, k: "line"});
  }
  if (wallet) out.push({t: c.walletNote, k: "note"});
  out.push({t: `As of ${ecDay(c.asOf)}`, k: "date"});
  return out;
}
/* a canvas the size of this screen in device pixels, upright: white on black, the lines in the lower two thirds (the
   clock and notifications have the top), in the largest type that fits, never smaller than EC_MIN of the width. When
   even that doesn't fit, the lines start higher (below the clock), then allergies are cut short ("and N more
   allergies", the first kept), then contacts (the first always kept), then the rest of the allergies: it never runs
   off the bottom. The canvas says what it shows: lines (their words), size (the type, px) and end (the last line's
   foot, px). */
const EC_MIN = 1 / 26;
function ecCanvas(c, wallet) {
  const r = Math.min(window.devicePixelRatio || 1, 4), a = Math.round(screen.width * r), b = Math.round(screen.height * r);
  const W = Math.min(a, b), H = Math.max(a, b), cv = el("canvas", {width: W, height: H}), g = cv.getContext("2d");
  const font = getComputedStyle(document.body).fontFamily || "sans-serif";
  // each kind's [size, weight, space above (in lines)]; rows 1.25 lines apart
  const KIND = {title: [1.05, 700, 0], name: [1.6, 700, 0.1], head: [0.8, 700, 0.9], line: [1, 400, 0], note: [0.9, 400, 0.9], date: [0.7, 400, 1.2]};
  const left = W * 0.07, room = W * 0.86, foot = H * 0.95;
  const wrap = (t, k, s) => {   // the line broken into what fits the width, at size s; a phone number after ": " kept whole
    const [f, w] = KIND[k];
    g.font = `${w} ${Math.round(s * f)}px ${font}`;
    const out = [];
    let line = "";
    const at = t.lastIndexOf(": "), ph = at < 0 ? "" : t.slice(at + 2);   // a contact's phone: what follows ": ", no letters in it
    const kept = /\d/.test(ph) && !/\p{L}/u.test(ph) ? t.slice(0, at + 2) + ph.replace(/ /g, "\u00a0") : t;
    for (const word of kept.split(" ")) {
      const next = line ? line + " " + word : word;
      if (line && g.measureText(next).width > room) { out.push(line); line = word; } else line = next;
    }
    return [...out, line];
  };
  const height = (laid, s) => laid.reduce((h, l) => h + Math.round(s * KIND[l.k][0]) * (KIND[l.k][2] + 1.25 * l.rows.length), 0);
  const count = name => (c.short.find(x => x.heading === name) || {lines: []}).lines.length;
  let keep = {}, s, top, laid;
  const fit = () => {   // the largest type down to the least that fits, the lines lower down while they can be
    for (s = W / 15; ; s *= 0.94) {
      laid = ecImageLines(c, wallet, keep).map(l => ({...l, rows: wrap(l.t, l.k, s)}));
      const h = height(laid, s);
      if (h <= foot - H * 0.36) { top = H * 0.36; return true; }
      if (s * 0.94 < W * EC_MIN) { top = Math.max(H * 0.12, foot - h); return h <= foot - H * 0.12; }
    }
  };
  const trim = (name, least) => { while (!fit() && (keep[name] ?? count(name)) > least) keep[name] = (keep[name] ?? count(name)) - 1; };
  if (!fit()) { trim("Allergies", 1); trim("Emergency contacts", 1); trim("Allergies", 0); }
  const tok = getComputedStyle(document.documentElement);   // white on black whatever the theme (tokens.css --lock-*)
  g.fillStyle = tok.getPropertyValue("--lock-bg").trim(); g.fillRect(0, 0, W, H);
  g.fillStyle = tok.getPropertyValue("--lock-ink").trim(); g.textBaseline = "alphabetic";
  let y = top;
  for (const l of laid) {
    const [f, w, above] = KIND[l.k], px = Math.round(s * f);
    g.font = `${w} ${px}px ${font}`;
    y += px * above;
    for (const row of l.rows) { y += px * 1.25; g.fillText(row, left, y - px * 0.2, room); }
  }
  return Object.assign(cv, {lines: laid.map(l => l.t), size: s, end: y});
}
