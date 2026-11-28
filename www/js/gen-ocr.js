/* Sanguity · Settings → Install → General → Text recognition (#1129): scanned documents read on this server
   (server/ocr.py), so a search finds them by their words. How far the reading of every document has got, the
   languages in use (English always, built in; each other one added, used once ticked: the install setting
   ocr.languages), Import a language file (a .traineddata, sent as it is: PUT settings/install/ocr/languages/{name}),
   and downloading one from the tesseract-ocr project, only once its switch is on (ocr.downloads; Outside connections
   lists its host). Each change after the fresh check, in the card (admin.js fresh). call() and plural() are
   settings.js's; admin.js starts it (loadGenOcr). */
"use strict";
let OCR_POLL = 0;
async function loadGenOcr(note) {
  const box = $("#genOcr"), msg = $("#genOcrMsg");
  let d;
  try { d = await call("settings/install/ocr"); }
  catch (e) { box.replaceChildren(); sayIn(msg, e.message, true); return; }
  drawGenOcr(d);
  if (note) sayIn(msg, note);
}
const ocrFresh = action => fresh(action, $("#genOcrReauth"));
function ocrProgress(d) {
  const p = d.pass, n = x => x.toLocaleString("en-US");
  if (!d.available) return "This server has no Tesseract, so no document is read. Sanguity's Docker images have it.";
  if (!p.total) return "No scanned document or picture yet: each is read as it's added.";
  const parts = [p.found && `${n(p.found)} with text found`, p.none && `${n(p.none)} with none (a photo, a blank page)`, p.failed && `${n(p.failed)} that couldn't be read`].filter(Boolean).join(", ");
  return (p.waiting ? `${n(p.read)} of ${plural(p.total, "document")} read, the rest in the background, newest first` : `All ${plural(p.total, "document")} read`)
    + (parts ? `: ${parts}.` : ".") + ` Each is read for at most ${d.max_pages} pages and ${d.max_minutes} minutes.`;
}
function drawGenOcr(d) {
  clearTimeout(OCR_POLL);
  const btn = (text, onclick, cls = "btn") => el("button", {type: "button", class: cls, text, onclick});
  const use = new Set(d.in_use);
  // (#1119) the built-in language is always used: said as text, never a disabled checkbox
  const langs = d.languages.map(l => el("div", {class: "form inline"},
    l.built_in ? el("p", {"data-code": l.code, text: `${l.name}, built in: always used`}) : el("label", {class: "check"}, el("input", {type: "checkbox", "data-code": l.code,
      checked: use.has(l.code) ? "" : null, onchange: ev => ocrUse(d, ev.target)}), el("span", {text: `${l.name} (${l.code})`})),
    l.built_in ? null : btn("Remove", () => ocrRemove(l), "btn small")));
  const file = el("input", {type: "file", id: "ocrFile", accept: ".traineddata", "aria-labelledby": "ocrFileLbl", "aria-describedby": "ocrFileH", onchange: () => ocrImport(file)});
  const dl = el("input", {type: "checkbox", role: "switch", id: "ocrDl", checked: d.downloads ? "" : null, "aria-labelledby": "ocrDlLbl", "aria-describedby": "ocrDlH",
    onchange: () => ocrDownloads(dl)});
  const pick = el("select", {id: "ocrPick", "aria-label": "Language to download"}, ...d.offered.map(o => el("option", {value: o.code, text: `${o.name} (${o.code})`})));
  $("#genOcr").replaceChildren(
    setRow([el("p", {class: "set-row-name", text: "Reading documents"})], el("p", {id: "ocrPass", text: ocrProgress(d)})),
    setRow([el("p", {class: "set-row-name", id: "ocrUseLbl", text: "Languages in use"}),
      el("p", {class: "hint", text: "Each language a document may be in. More of them make reading slower. A change applies to documents read from then on."})],
      el("div", {role: "group", "aria-labelledby": "ocrUseLbl"}, ...langs)),
    setRow([el("p", {class: "set-row-name", id: "ocrFileLbl", text: "Import a language file"}),
      el("p", {class: "hint", id: "ocrFileH", text: `A Tesseract .traineddata file named for its language (deu.traineddata), at most ${d.max_mb} MB: from the tesseract-ocr project's tessdata_fast, tessdata or tessdata_best on GitHub. It's tried once before it's kept.`})],
      el("label", {class: "btn pick"}, "Choose a file…", file)),   // the app's file picker (#353)
    setRow([el("p", {class: "set-row-name", id: "ocrDlLbl", text: "Download languages from the tesseract-ocr project"}),
      el("p", {class: "hint", id: "ocrDlH", text: `Off: nothing is downloaded. On: an admin can download a language here, from ${d.host} (listed under Outside connections), only when they press Download.`})],
      [el("label", {class: "check switch"}, dl), d.downloads ? el("div", {class: "form inline"}, pick, btn("Download", ev => ocrDownload(ev.target, pick))) : null]));
  if (d.available && d.pass.waiting && $("#gen-ocr").isConnected)   // its progress, while documents are being read
    OCR_POLL = setTimeout(async () => { try { const n = await call("settings/install/ocr", "GET", undefined, background()); $("#ocrPass").textContent = ocrProgress(n); if (n.pass.waiting) drawGenOcr(n); } catch (e) {} }, 15000);
}
async function ocrUse(d, box) {
  const code = box.dataset.code, now = d.in_use.filter(c => c !== "eng"), value = box.checked ? [...now, code] : now.filter(c => c !== code);
  try {
    await ocrFresh(() => call("settings/install/ocr.languages", "PUT", {value}));
    await loadGenOcr(box.checked ? `${code} is used from now on.` : `${code} isn't used from now on.`);
  } catch (e) { box.checked = !box.checked; sayIn($("#genOcrMsg"), `Not changed: ${e.message}`, true); }
  document.querySelector(`#genOcr [data-code="${code}"]`)?.focus();
}
async function ocrImport(file) {
  const f = file.files[0];
  if (!f) return;
  sayIn($("#genOcrMsg"), `Checking ${f.name}…`);
  try {
    const r = await ocrFresh(() => call(`settings/install/ocr/languages/${encodeURIComponent(f.name)}`, "PUT", undefined,
      {body: f, headers: {"Content-Type": "application/octet-stream"}}));
    drawGenOcr(r);
    sayIn($("#genOcrMsg"), `Added ${r.added.name}. Tick it under Languages in use to read documents with it.`);
  } catch (e) { file.value = ""; sayIn($("#genOcrMsg"), e.message, true); }
  $("#ocrFile")?.focus();
}
async function ocrRemove(l) {
  if (!await ask({title: `Remove ${l.name}?`, action: "Remove", message: "Documents already read keep their text; those read from now on aren't read with it."})) return;
  try { await ocrFresh(() => call(`settings/install/ocr/languages/${encodeURIComponent(l.code)}`, "DELETE")); }
  catch (e) { sayIn($("#genOcrMsg"), e.message, true); return; }
  await loadGenOcr(`Removed ${l.name}.`);
  $("#ocrFile")?.focus();
}
async function ocrDownloads(sw) {
  const on = sw.checked;
  try { await ocrFresh(() => call("settings/install/ocr.downloads", "PUT", {value: on})); }
  catch (e) { sw.checked = !on; sayIn($("#genOcrMsg"), `Not changed: ${e.message}`, true); return; }
  await loadGenOcr(on ? "Languages can be downloaded here now." : "Nothing is downloaded now.");
  SgOutside.load();   // (its host listed there, or not)
  $("#ocrDl")?.focus();
}
async function ocrDownload(b, pick) {
  b.disabled = true;
  sayIn($("#genOcrMsg"), "Downloading…");
  try {
    const r = await ocrFresh(() => call("settings/install/ocr/download", "POST", {code: pick.value}));
    drawGenOcr(r);
    sayIn($("#genOcrMsg"), `Added ${r.added.name}. Tick it under Languages in use to read documents with it.`);
  } catch (e) { b.disabled = false; sayIn($("#genOcrMsg"), e.message, true); }
  SgOutside.load();
  $("#ocrPick")?.focus();
}
