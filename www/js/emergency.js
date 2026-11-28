/* Sanguity · the emergency card (#1126, #1162), on Sharing → Emergency card. Nothing goes on it by itself: owners and
   editors make it here, in its editor, from what the records suggest (each with Add) and lines of their own, and save
   it (server/ecard.py keeps it, encrypted). Beside the editor, a preview of each file, as the card being edited would
   make it (POST emergency/preview, a moment after each change), and the files made from the saved card (unsaved
   changes are saved first), each a file the person keeps (no address opens it, no share link carries it):
   - the printed card, a PDF the server makes (server/emergency.py): a full page or a wallet card to fold, on Letter
     or A4 (the browser's region chooses at first);
   - the lock-screen image, a PNG drawn here on a canvas from the card's lock-screen sections, sized for this screen
     and upright, "More: see the card in my wallet" only with "I carry the printed card" on (off each time). A
     canvas's PNG holds the picture and nothing else: no text, no time, no place.
   The QR code (off at first) holds the card's own words (the server chooses them, at most 800 bytes, and says what
   didn't fit): qr.js draws it here, for the preview and the lock screen, and sends its modules for the PDF.
   Viewers see the saved card and make its files. drawEmergency(p) draws it for p (sharing.js, when the group is shown
   or the profile changes); call() and api() are the page's. Nothing here is put into innerHTML. Plain script; all
   files share one scope. */
"use strict";
let EC = null;   // {p, got: GET emergency's answer, card: the card being edited, view: its preview, dirty}
const EC_LETTER = /-(US|CA|MX|PH|CL|CO|VE|GT|CR|PA|DO|PR|SV|NI|HN|BZ)$/i;   // regions that print on Letter
const EC_QR_MOST = 23;   // the QR code's largest version: 857 bytes at level M, small enough to scan at wallet size
const EC_FROM = {records: "From the records", about: "From About this person", insurance: "From Insurance cards"};
const EC_EXAMPLE = {alerts: "Such as: Irradiated blood products only", contacts: "Name (relationship): phone",
  careteam: "Name, clinic: phone (24-hour line)", devices: "Such as: Port, right chest"};
async function drawEmergency(p) {
  const was = EC;
  if (was && was.p === p && (was.loading || was.dirty)) return;   // drawn again while it loads, or with changes not saved: kept
  EC = {p, loading: true};
  sayIn($("#ecMsg"), "");
  let got;
  try { got = await call(`profiles/${p.id}/emergency`); }
  catch (e) { if (EC.p === p) { EC.loading = false; $("#ecBody").replaceChildren(); sayIn($("#ecMsg"), e.message, true); } return; }
  if (EC.p !== p) return;
  EC.loading = false;
  ecTake(got, was && was.p === p ? was : null);
  $("#emergency").dataset.profile = p.id;   // whose card is drawn (the browser check waits for it)
}
/* the answer of GET or PUT emergency, drawn: the card as saved, nothing changed yet */
function ecTake(got, was) {
  Object.assign(EC, {got, card: structuredClone(got.card), view: got.view, dirty: false});
  const paper = was?.paper || (EC_LETTER.test(navigator.language || "") ? "letter" : "a4");
  Object.assign(EC, {paper, form: was?.form || "page", wallet: false});
  $("#ecBody").replaceChildren(el("div", {class: "ec-wrap"}, el("div", {class: "ec-edit", id: "ecEdit"}), el("div", {class: "ec-out", id: "ecOut"})));
  ecEditor();
  ecFiles();
}
const ecDay = d => fmtD(dayTs(d));
const ecSec = id => EC.got.sections.find(s => s.id === id);
function ecChanged(redraw, focus) {
  EC.dirty = true;
  $("#ecUnsaved").hidden = false;
  if (redraw) ecEditor(focus);
  clearTimeout(EC.timer);
  EC.timer = setTimeout(ecPreview, 250);
}
async function ecPreview() {
  const p = EC.p;
  try { const v = await call(`profiles/${p.id}/emergency/preview`, "POST", {card: EC.card}); if (EC.p === p) { EC.view = v; ecShow(); } }
  catch (e) { if (EC.p === p) sayIn($("#ecMsg"), e.message, true); }
}

/* ---------------------------------------------------------------- the editor */
function ecEditor(focus) {
  const box = $("#ecEdit"), g = EC.got, c = EC.card, back = focus || (box.contains(document.activeElement) ? document.activeElement.id : null);
  const top = el("p", {id: "ecStatus", class: "hint"}, g.view.saved ? `Card last updated ${ecDay(g.view.saved)}.` : "Not saved yet: nothing is on the card until you add it.");
  if (!g.canEdit) {
    box.replaceChildren(top, el("p", {class: "hint", text: "Owners and editors change what's on it; you can make its files."}));
    return;
  }
  const fresh = g.newSince ? el("p", {class: "ec-new", id: "ecNew"}, el("span", {class: "tag", text: "New"}), " ",
    `${g.newSince} new suggestion${g.newSince === 1 ? "" : "s"} since you saved this card. Nothing on the card changed: add them below if you want them.`) : null;
  const name = el("input", {id: "ecName", maxlength: 120, autocomplete: "name", value: c.name, placeholder: EC.p.name,
    "aria-describedby": "ecNameHint", oninput: ev => { c.name = ev.target.value; ecChanged(); }});
  const order = c.order.map((id, i) => ecSection(id, i));
  const qr = el("input", {type: "checkbox", role: "switch", id: "ecQr", checked: c.qr.on, onchange: ev => { c.qr.on = ev.target.checked; c.qr.lock = c.qr.lock && c.qr.on; ecChanged(true, "ecQr"); }});
  const qrLock = el("input", {type: "checkbox", id: "ecQrLock", checked: c.qr.lock, disabled: !c.qr.on || null, onchange: ev => { c.qr.lock = ev.target.checked; ecChanged(); }});
  box.replaceChildren(...[top, fresh].filter(Boolean),
    el("div", {class: "ec-name"}, el("label", {for: "ecName", text: "Full legal name"}), name,
      el("p", {class: "hint", id: "ecNameHint", text: `Leave it empty for the profile's name, ${EC.p.name}.`})),
    ...order,
    el("fieldset", {class: "ec-sec", id: "ecQrSec"}, el("legend", {text: "QR code"}),
      el("label", {class: "check switch"}, qr, "Add a QR code of the card's text"),
      el("p", {class: "hint", text: "Off at first. It holds the card's own words, read offline by any phone's camera: never a link, nothing online."}),
      el("label", {class: "check"}, qrLock, "On the lock-screen image too"),
      el("p", {class: "hint", id: "ecQrLeft", "aria-live": "polite"})),
    el("div", {class: "row ec-save"}, el("button", {class: "btn primary", type: "button", id: "ecSave", text: "Save the card", onclick: ecSave}),
      el("span", {class: "hint", id: "ecUnsaved", hidden: !EC.dirty || null, text: "Changes not saved yet."})));
  ecQrLeft();
  const f = back && document.getElementById(back);
  if (f) f.focus({preventScroll: false});
}
/* one section: where it goes, moved up or down (Critical alerts stay first), its lines (reworded, moved, removed), a
   line of one's own, and what the records suggest for it */
function ecSection(id, at) {
  const c = EC.card, s = c.sections[id], meta = ecSec(id), h = meta.heading, sugs = (EC.got.suggestions || []).filter(x => x.section === id);
  const box = (k, label) => el("label", {class: "check"}, el("input", {type: "checkbox", id: `ec${k}-${id}`, checked: s[k.toLowerCase()],
    onchange: ev => { s[k.toLowerCase()] = ev.target.checked; ecChanged(); }}), label);
  const moveSec = by => { const o = c.order, j = at + by; [o[at], o[j]] = [o[j], o[at]];
    sayIn($("#ecMsg"), `Moved ${h} ${by < 0 ? "up" : "down"}.`); ecChanged(true, document.getElementById(`ecSecUp-${id}`) && by < 0 && j > 1 ? `ecSecUp-${id}` : by > 0 && j < c.order.length - 1 ? `ecSecDown-${id}` : `ecSecHd-${id}`); };
  const head = el("div", {class: "ec-place"}, box("Print", "Printed card"), box("Lock", "Lock screen"),
    id === "alerts" ? null : el("span", {class: "ec-moves"},
      at > 1 ? el("button", {class: "btn small", type: "button", id: `ecSecUp-${id}`, "aria-label": `Move ${h} up`, onclick: () => moveSec(-1)}, icon("arrow-up")) : null,
      at < c.order.length - 1 ? el("button", {class: "btn small", type: "button", id: `ecSecDown-${id}`, "aria-label": `Move ${h} down`, onclick: () => moveSec(1)}, icon("arrow-down")) : null));
  const body = id === "details" ? ecDetails(sugs) : ecLines(id, h, sugs);
  const note = id === "contacts" || id === "blood" ? el("p", {class: "hint"}, `${id === "contacts" ? "Emergency contacts" : "A blood type"} typed in Settings → `,
    el("a", {href: `settings.html?profile=${EC.p.id}#about`, text: "About this person"}), " are suggested here.") : null;
  return el("fieldset", {class: "ec-sec" + (id === "alerts" ? " ec-alerts" : ""), id: `ecSec-${id}`},
    el("legend", {id: `ecSecHd-${id}`, tabindex: -1, text: h}), head, note, ...body);
}
function ecLines(id, h, sugs) {
  const lines = EC.card.sections[id].lines, gone = new Set(EC.got.gone || []);
  const move = (i, by) => { [lines[i], lines[i + by]] = [lines[i + by], lines[i]]; sayIn($("#ecMsg"), `Moved line ${i + 1} of ${h} ${by < 0 ? "up" : "down"}.`);
    ecChanged(true, by < 0 ? (i + by > 0 ? `ecUp-${id}-${i + by}` : `ecDown-${id}-0`) : (i + by < lines.length - 1 ? `ecDown-${id}-${i + by}` : `ecUp-${id}-${i + by}`)); };
  const remove = i => { const [x] = lines.splice(i, 1), back = EC.taken?.[x.from];   // a suggestion's line: suggested again
    if (back) { EC.got.suggestions.push(back); delete EC.taken[x.from]; }
    sayIn($("#ecMsg"), `Removed “${x.text}” from ${h}.`);
    ecChanged(true, lines.length ? `ecLine-${id}-${Math.min(i, lines.length - 1)}` : `ecAdd-${id}`); };
  const list = lines.length ? el("ol", {class: "ec-lines"}, ...lines.map((x, i) => el("li", {},
    el("input", {id: `ecLine-${id}-${i}`, maxlength: 300, value: x.text, "aria-label": `${h}, line ${i + 1}`,
      oninput: ev => { x.text = ev.target.value; ecChanged(); }}),
    gone.has(x.from) ? el("span", {class: "hint", text: "Not in the records now"}) : null,
    el("span", {class: "ec-moves"},
      i > 0 ? el("button", {class: "btn small", type: "button", id: `ecUp-${id}-${i}`, "aria-label": `Move line ${i + 1} of ${h} up`, onclick: () => move(i, -1)}, icon("arrow-up")) : null,
      i < lines.length - 1 ? el("button", {class: "btn small", type: "button", id: `ecDown-${id}-${i}`, "aria-label": `Move line ${i + 1} of ${h} down`, onclick: () => move(i, 1)}, icon("arrow-down")) : null,
      el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": `Remove line ${i + 1} of ${h}`, onclick: () => remove(i)})))))
    : el("p", {class: "muted", text: "Nothing here yet: it's left off the card."});
  const add = el("input", {id: `ecAdd-${id}`, maxlength: 300, "aria-label": `A line of your own for ${h}`, placeholder: EC_EXAMPLE[id] || "", autocomplete: "off"});
  const form = el("form", {class: "ec-addrow", onsubmit: ev => { ev.preventDefault();
    const t = add.value.trim(); if (!t) { add.focus(); return; }
    lines.push({text: t, from: ""}); sayIn($("#ecMsg"), `Added “${t}” to ${h}.`); ecChanged(true, `ecAdd-${id}`); }},
    add, el("button", {class: "btn", type: "submit", text: "Add", "aria-label": `Add your line to ${h}`}));
  return [list, ecSuggested(sugs, h, (x, next) => { lines.push({text: x.text, from: x.key}); sayIn($("#ecMsg"), `Added “${x.text}” to ${h}.`); ecChanged(true, next || `ecLine-${id}-${lines.length - 1}`); }), form];
}
/* the suggestions for a section, each with Add (focus then goes to the next one, so several are quick to add) */
function ecSuggested(sugs, h, take) {
  if (!sugs.length) return null;
  const gone = x => { EC.got.suggestions.splice(EC.got.suggestions.indexOf(x), 1); (EC.taken ||= {})[x.key] = x; };
  return el("div", {class: "ec-sugs"}, el("p", {class: "hint", text: "Suggested from your records and settings:"}),
    el("ul", {}, ...sugs.map((x, i) => el("li", {},
      el("span", {}, x.new ? el("span", {class: "tag", text: "New"}) : null, x.new ? " " : null, x.field ? `${EC.got.details.find(d => d.id === x.field).label}: ` : "", x.text,
        el("span", {class: "hint", text: ` · ${EC_FROM[x.from] || ""}`})),
      el("button", {class: "btn small", type: "button", id: `ecSug-${x.key}`, text: x.field ? "Use" : "Add",
        "aria-label": `${x.field ? "Use" : "Add"} “${x.text}” ${x.field ? "for" : "to"} ${h}`,
        onclick: () => { gone(x); take(x, sugs[i + 1] ? `ecSug-${sugs[i + 1].key}` : null); }})))));
}
function ecDetails(sugs) {
  const c = EC.card, d = c.details;
  const field = ({id, label}) => {
    const attrs = {id: `ecDet-${id}`, maxlength: 120, autocomplete: "off", oninput: ev => { d[id] = ev.target.value; delete c.detailsFrom[id]; ecChanged(); }};
    const ctl = id === "donor" ? el("select", {...attrs, onchange: attrs.oninput, oninput: null},
      ...[["", "Not said"], ["Yes", "Yes"], ["No", "No"]].map(([v, t]) => el("option", {value: v, text: t, selected: d[id] === v})))
      : el("input", {...attrs, value: d[id]});
    return el("div", {class: "ec-det"}, el("label", {for: `ecDet-${id}`, text: label}), ctl);
  };
  return [el("div", {class: "ec-dets"}, ...EC.got.details.map(field)),
    ecSuggested(sugs, "Personal details", (x, next) => { d[x.field] = x.text; c.detailsFrom[x.field] = x.key;
      sayIn($("#ecMsg"), `${EC.got.details.find(f => f.id === x.field).label}: ${x.text}.`); ecChanged(true, next || `ecDet-${x.field}`); })];
}
async function ecSave() {
  const b = $("#ecSave");
  if (b.getAttribute("aria-disabled") === "true") return false;
  b.setAttribute("aria-disabled", "true");
  try {
    clearTimeout(EC.timer);
    const got = await call(`profiles/${EC.p.id}/emergency`, "PUT", {card: EC.card});
    ecTake(got, EC);
    $("#ecSave").focus();
    sayIn($("#ecMsg"), `Saved. Card last updated ${ecDay(got.view.saved)}.`);
    return true;
  } catch (e) { sayIn($("#ecMsg"), e.message, true); return false; }
  finally { $("#ecSave")?.removeAttribute("aria-disabled"); }
}
function ecQrLeft() {
  const q = EC.view.qr, m = $("#ecQrLeft");
  if (!m) return;
  m.textContent = !q.on ? "" : !q.left.length ? `It holds all of the card (at most ${q.most} characters).`
    : `Not in it (it holds at most ${q.most} characters, so it scans at wallet size): ` + q.left.map(x => x.left === x.of ? x.heading.toLowerCase() : `${x.left} of ${x.of} ${x.heading.toLowerCase()}`).join(", ") + ".";
}

/* ---------------------------------------------------------------- the previews and the files */
function ecFiles() {
  const paper = el("select", {id: "ecPaper", onchange: ev => { EC.paper = ev.target.value; }},
    el("option", {value: "letter", text: "Letter"}), el("option", {value: "a4", text: "A4"}));
  paper.value = EC.paper;
  const form = el("select", {id: "ecForm", onchange: ev => { EC.form = ev.target.value; ecShow(); }},
    el("option", {value: "page", text: "Full page"}), el("option", {value: "wallet", text: "Wallet card to fold"}));
  form.value = EC.form;
  const wallet = el("input", {type: "checkbox", role: "switch", id: "ecWallet", onchange: ev => { EC.wallet = ev.target.checked; ecShow(); }});
  $("#ecOut").replaceChildren(
    el("h3", {class: "sub", text: "Printed card (PDF)"}),
    el("div", {class: "ec-pick"}, el("label", {}, "Format ", form), el("label", {}, "Paper ", paper)),
    el("div", {id: "ecPrint", class: "ec-paper", role: "img"}),
    el("div", {class: "row"}, el("button", {class: "btn primary", type: "button", id: "ecPdf", text: "Download PDF", onclick: ev => ecPdf(ev.currentTarget)})),
    el("h3", {class: "sub", text: "Lock-screen image (PNG)"}),
    el("p", {class: "hint", text: "Sized for this screen, to set as your phone's lock screen: the sections ticked for it."}),
    el("label", {class: "check switch"}, wallet, "I carry the printed card"),
    el("p", {class: "hint", text: `Adds “${EC.got.walletNote}” to the image.`}),
    el("img", {id: "ecImg", class: "ec-img", alt: ""}),
    el("p", {class: "hint", id: "ecCut"}),
    el("div", {class: "row"}, el("button", {class: "btn", type: "button", id: "ecPng", text: "Download image", onclick: ev => ecPng(ev.currentTarget)})));
  ecShow();
}
/* the previews, from the card being edited: the printed card as HTML (the PDF's sections, in its order), the lock-screen
   image as it will be drawn */
function ecShow() {
  const v = EC.view, parts = v.parts.filter(x => x.print), wallet = EC.form === "wallet";
  const box = $("#ecPrint");
  if (!box) return;
  box.classList.toggle("wallet", wallet);
  box.setAttribute("aria-label", `Preview of the printed card${wallet ? ", wallet format" : ""}`);
  const qr = v.qr.on && v.qr.text ? qrSvg(v.qr.text, {label: "The card's text as a QR code", most: EC_QR_MOST}) : null;
  box.replaceChildren(el("p", {class: "ec-p-title", text: wallet ? "EMERGENCY" : "EMERGENCY CARD"}), el("p", {class: "ec-p-name", text: v.name}),
    el("p", {class: "ec-p-date", text: v.updated}),
    ...parts.length ? parts.map(x => el("div", {class: "ec-p-part" + (x.id === "alerts" ? " ec-alerts" : "")},
      el("p", {class: "ec-p-head", text: x.heading.toUpperCase()}), ...x.lines.map(t => el("p", {text: t}))))
      : [el("p", {class: "muted", text: "Nothing on the printed card yet."})],
    ...qr ? [el("div", {class: "ec-p-qr"}, qr, el("p", {text: "Scan with a phone's camera: the card's text, read offline. It isn't a link."}))] : []);
  const cv = ecCanvas(v, EC.wallet), img = $("#ecImg");
  img.src = cv.toDataURL("image/png");
  img.alt = "Preview of the lock-screen image: " + cv.lines.join(". ");   // (what it shows, cut short or not)
  $("#ecCut").textContent = cv.cut.length ? "Cut to fit the screen: " + cv.cut.map(x => `${x.left} of ${x.of} ${x.heading.toLowerCase()}`).join(", ") + " (“and N more” says so on it)." : "";
  ecQrLeft();
}

/* a file saved from memory, as the visit summary's (summary.js) */
function ecSaveFile(blob, name) {
  const url = URL.createObjectURL(blob), a = el("a", {href: url, download: name, hidden: true});
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  sayIn($("#ecMsg"), `Downloaded ${name}.`);
}
/* not disabled while it's made (aria-disabled): focus stays on the button. Unsaved changes are saved first. */
async function ecBusy(b, said, work) {
  if (b.getAttribute("aria-disabled") === "true") return;
  if (EC.dirty && !(await ecSave())) return;
  b = document.getElementById(b.id) || b;
  b.focus();
  b.setAttribute("aria-disabled", "true"); sayIn($("#ecMsg"), said);
  try { await work(); } catch (e) { sayIn($("#ecMsg"), e.message, true); } finally { b.removeAttribute("aria-disabled"); }
}
const ecModules = v => v.qr.on && v.qr.text ? qrCode(v.qr.text, {most: EC_QR_MOST})?.modules.map(r => r.join("")) || null : null;
const ecPdf = b => ecBusy(b, "Making the PDF…", async () => {
  const v = EC.got.view;
  const r = await api(`profiles/${EC.p.id}/emergency/pdf`, {method: "POST", body: JSON.stringify({paper: EC.paper, form: EC.form, qr: ecModules(v)})});
  if (r.status === 401) { location.replace("login.html"); return; }
  if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error((typeof j.detail === "string" && j.detail) || `Not made (${r.status}).`); }
  ecSaveFile(await r.blob(), (/filename="([^"]+)"/.exec(r.headers.get("content-disposition") || "") || [])[1] || "emergency-card.pdf");
});
const ecPng = b => ecBusy(b, "Making the image…", async () => {
  const v = await call(`profiles/${EC.p.id}/emergency/image`, "POST", {wallet: EC.wallet});   // the card as saved (and logged)
  const blob = await new Promise(ok => ecCanvas(v, EC.wallet).toBlob(ok, "image/png"));
  if (!blob) throw new Error("This browser couldn't make the image.");
  ecSaveFile(blob, v.filename);
});

/* ---------------------------------------------------------------- the lock-screen image */
/* its lines, top to bottom: [{t, k}], k the kind ("title", "name", "head", "alert", "line", "note", "date"). keep: how
   many of a section's lines are shown ({allergies: n}; all, if not said), the rest said as "and N more allergies" */
function ecImageLines(v, wallet, keep = {}) {
  const out = [{t: "EMERGENCY INFORMATION", k: "title"}, {t: v.name, k: "name"}];
  for (const s of v.parts.filter(x => x.lock)) {
    const n = Math.min(keep[s.id] ?? s.lines.length, s.lines.length), more = s.lines.length - n;
    out.push({t: s.heading.toUpperCase(), k: "head"}, ...s.lines.slice(0, n).map(t => ({t, k: s.id === "alerts" ? "alert" : "line"})));
    if (more) out.push({t: `and ${more} more ${s.nouns[more === 1 ? 0 : 1]}`, k: "line"});
  }
  if (wallet) out.push({t: EC?.got?.walletNote || "More: see the card in my wallet", k: "note"});
  out.push({t: v.updated, k: "date"});
  return out;
}
/* a canvas the size of this screen in device pixels, upright: white on black, the lines in the lower two thirds (the
   clock and notifications have the top), in the largest type that fits, never smaller than EC_MIN of the width; the QR
   code, when the card has one for the lock screen, under them, dark on a light square. When even that doesn't fit,
   the lines start higher (below the clock), then sections are cut short ("and N more", the first line kept): the
   others first, then allergies, then contacts (the first always kept), then critical alerts; then the others go, and
   the rest of the allergies: it never runs off the bottom. The canvas says what it shows: lines (their words), cut
   ([{heading, left, of}]), size (the type, px) and end (the last line's foot, px). */
const EC_MIN = 1 / 26;
function ecCanvas(v, wallet) {
  const r = Math.min(window.devicePixelRatio || 1, 4), a = Math.round(screen.width * r), b = Math.round(screen.height * r);
  const W = Math.min(a, b), H = Math.max(a, b), cv = el("canvas", {width: W, height: H}), g = cv.getContext("2d");
  const font = getComputedStyle(document.body).fontFamily || "sans-serif";
  // each kind's [size, weight, space above (in lines)]; rows 1.25 lines apart
  const KIND = {title: [1.05, 700, 0], name: [1.6, 700, 0.1], head: [0.8, 700, 0.9], alert: [1, 700, 0], line: [1, 400, 0], note: [0.9, 400, 0.9], date: [0.7, 400, 1.2]};
  const q = v.qr.on && v.qr.lock && v.qr.text ? qrCode(v.qr.text, {most: EC_QR_MOST}) : null, qside = q ? Math.round(W * 0.46) : 0;
  const left = W * 0.07, room = W * 0.86, foot = H * 0.95 - (q ? qside + H * 0.02 : 0);
  const wrap = (t, k, s) => {   // the line broken into what fits the width, at size s; a phone number after ": " kept whole
    const [f, w] = KIND[k];
    g.font = `${w} ${Math.round(s * f)}px ${font}`;
    const out = [];
    let line = "";
    const at = t.lastIndexOf(": "), ph = at < 0 ? "" : t.slice(at + 2);   // a contact's phone: what follows ": ", no letters in it
    const kept = /\d/.test(ph) && !/\p{L}/u.test(ph) ? t.slice(0, at + 2) + ph.replace(/ /g, " ") : t;
    for (const word of kept.split(" ")) {
      const next = line ? line + " " + word : word;
      if (line && g.measureText(next).width > room) { out.push(line); line = word; } else line = next;
    }
    return [...out, line];
  };
  const height = (laid, s) => laid.reduce((h, l) => h + Math.round(s * KIND[l.k][0]) * (KIND[l.k][2] + 1.25 * l.rows.length), 0);
  const lock = v.parts.filter(x => x.lock), count = id => (lock.find(x => x.id === id) || {lines: []}).lines.length;
  let keep = {}, s, top, laid;
  const fit = () => {   // the largest type down to the least that fits, the lines lower down while they can be
    for (s = W / 15; ; s *= 0.94) {
      laid = ecImageLines(v, wallet, keep).map(l => ({...l, rows: wrap(l.t, l.k, s)}));
      const h = height(laid, s);
      if (h <= foot - H * 0.36) { top = H * 0.36; return true; }
      if (s * 0.94 < W * EC_MIN) { top = Math.max(H * 0.12, foot - h); return h <= foot - H * 0.12; }
    }
  };
  const trim = (id, least) => { while (!fit() && (keep[id] ?? count(id)) > least) keep[id] = (keep[id] ?? count(id)) - 1; };
  const others = lock.map(x => x.id).filter(id => !["alerts", "allergies", "contacts"].includes(id)).reverse();
  if (!fit()) {
    for (const id of others) trim(id, 1);
    trim("allergies", 1); trim("contacts", 1); trim("alerts", 1);
    for (const id of others) trim(id, 0);
    trim("allergies", 0);
  }
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
  if (q) {   // dark modules on a light square (lock-ink), its quiet zone inside it: readable by any camera
    const x0 = Math.round((W - qside) / 2), y0 = Math.round(H * 0.95 - qside), m = qside / (q.size + 8);
    g.fillRect(x0, y0, qside, qside);
    g.fillStyle = tok.getPropertyValue("--lock-bg").trim();
    q.modules.forEach((row, rr) => row.forEach((d, cc) => { if (d) g.fillRect(x0 + (cc + 4) * m, y0 + (rr + 4) * m, Math.ceil(m), Math.ceil(m)); }));
  }
  const cut = lock.filter(x => (keep[x.id] ?? x.lines.length) < x.lines.length).map(x => ({heading: x.heading, left: x.lines.length - keep[x.id], of: x.lines.length}));
  return Object.assign(cv, {lines: laid.map(l => l.t), cut, size: s, end: y});
}
