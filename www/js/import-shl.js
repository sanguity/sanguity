/* Sanguity · import page, "Paste a link" tab (#94): a SMART Health Link or Health Card, pasted or scanned from its
   QR code (BarcodeDetector, where the browser has it). What the link says (its host, label, flags, expiry) is shown
   first, read by the server without contacting anyone; only Fetch and preview makes the one outside request. The
   files it brings become an upload, imported like any other: window.sanguityImport.uploaded (import-jobs.js) shows
   the review (#282), with its Add button and Cancel. $, el and api come from util.js. */
"use strict";
(function () {
  const text = $("#shlText"), msg = $("#shlMsg"), about = $("#shlAbout"), go = $("#shlGo"), goMsg = $("#shlGoMsg");
  const pass = $("#shlPass"), result = $("#shlResult");
  pwField(pass, {name: "passcode"});   // Show and Hide, as every password field has (util.js, #492)
  let seen = null, timer = 0, asked = 0, busy = false;

  const tell = (node, t, bad = false) => { node.textContent = t; node.classList.toggle("bad", bad); };
  const pid = () => +$("#profile").value;
  async function post(path, body) {
    const r = await api(`profiles/${pid()}/${path}`, {method: "POST", body: JSON.stringify(body)});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || `The server answered ${r.status}.`);
    return j;
  }
  const when = s => fmtDT(s * 1000);   // dates.js

  /* ---------------------------------------------------------------- what the link says: nothing fetched */
  function clear() { seen = null; about.hidden = true; pass.value = ""; }
  async function inspect() {
    const t = text.value.trim(), n = ++asked;
    result.replaceChildren();
    if (!t) { clear(); tell(msg, ""); return; }
    let d;
    try { d = await post("shl/inspect", {link: t}); }
    catch (e) { if (n === asked) { clear(); tell(msg, e.message, true); } return; }
    if (n !== asked) return;   // a newer paste is being read
    seen = {link: t, ...d};
    tell(msg, "");
    const rows = d.kind === "card"
      ? [["What", "A SMART Health Card (signed, carried in full in the code)"], ["Issuer", d.issuer || "not named"]]
      : [["Host", d.host], ...(d.label ? [["Label", d.label]] : []),
         ["Passcode", d.passcode ? "needed" : "not needed"], ["Kind", d.long_term ? "a long-term link (it may be updated later)" : "a one-time link"],
         ...(d.direct ? [["Holds", "a single file"]] : []),
         ["Expires", d.exp ? (d.expired ? `expired ${when(d.exp)}` : when(d.exp)) : "no expiry given"]];
    $("#shlFacts").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: v})]));
    $("#shlPassRow").hidden = !d.passcode;
    const who = d.kind === "card"
      ? (d.host ? `Fetch and preview checks the card's signature: Sanguity asks ${d.host} for its public keys. Nothing else is sent.`
                : "This card names no issuer, so its signature can't be checked.")
      : `Fetch and preview contacts ${d.host} once to download the records${d.passcode ? ", sending the passcode" : ""}. ` +
        "They're decrypted here, on your Sanguity server. The contact is noted in the profile's activity log.";
    tell($("#shlWho"), d.expired ? "This link has expired, so it can't be fetched. Ask whoever shared it for a new one."
                       : !d.https ? "This link's address isn't HTTPS, so Sanguity won't fetch it." : who, d.expired || !d.https);
    go.disabled = busy || d.expired || !d.https;
    tell(goMsg, "");
    about.hidden = false;
  }
  text.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(inspect, 250); });
  text.addEventListener("paste", () => { clearTimeout(timer); timer = setTimeout(inspect, 0); });
  $("#profile").addEventListener("change", () => { if (text.value.trim()) inspect(); });

  /* ---------------------------------------------------------------- fetching, once confirmed */
  go.addEventListener("click", async () => {
    if (!seen || busy) return;
    if (seen.passcode && !pass.value) { tell(goMsg, "Enter the link's passcode first.", true); pass.focus(); return; }
    busy = true; go.disabled = text.disabled = true; pwSent(about);
    tell(goMsg, seen.kind === "card" ? "Checking the card…" : `Fetching from ${seen.host}…`);
    try {
      const r = await post("shl/import", {link: seen.link, passcode: seen.passcode ? pass.value : null, confirm: true});
      pass.value = "";
      tell(goMsg, "Fetched. Reading the records…");
      const parts = (r.cards || []).map(c => el("p", {class: "shl-card"},
        c.signature === "verified" ? `Health Card from ${c.host}: signature verified.`
        : c.signature === "invalid" ? el("span", {class: "bad", text: `Health Card from ${c.host || "an unnamed issuer"}: the signature is not valid, so it was left out.`})
        : el("span", {class: "bad", text: `Health Card from ${c.host || "an unnamed issuer"}: its signature couldn't be checked (${c.why}). Import it only if you trust where it came from.`})));
      for (const n of r.notes || []) parts.push(el("p", {class: "shl-card", text: n}));
      result.replaceChildren(...parts);
      const title = seen.label || "A Health Link";
      text.value = ""; clear();
      const hook = window.sanguityImport && window.sanguityImport.uploaded;
      if (typeof hook === "function") await hook({profileId: pid(), uploadId: r.upload, importer: "fhir", title});
      tell(goMsg, "");
    } catch (e) {
      tell(goMsg, e.message, true);
    } finally {
      busy = false; text.disabled = false; go.disabled = !seen || seen.expired || !seen.https;
    }
  });

  /* ---------------------------------------------------------------- scanning a QR code, where the browser can */
  const scan = $("#shlScan"), video = $("#shlVideo");
  let stream = null;
  function stopScan() {
    if (stream) for (const t of stream.getTracks()) t.stop();
    stream = null; video.hidden = true; video.srcObject = null; scan.textContent = "Scan a QR code";
  }
  if ("BarcodeDetector" in window && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    Promise.resolve(BarcodeDetector.getSupportedFormats ? BarcodeDetector.getSupportedFormats() : ["qr_code"])
      .then(f => { scan.hidden = !f.includes("qr_code"); }).catch(() => {});
  }
  scan.addEventListener("click", async () => {
    if (stream) { stopScan(); tell(msg, ""); return; }
    let det;
    try {
      det = new BarcodeDetector({formats: ["qr_code"]});
      stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: "environment"}});
    } catch (e) { stopScan(); tell(msg, "The camera couldn't be opened. Paste the link instead.", true); return; }
    video.srcObject = stream; video.hidden = false; scan.textContent = "Stop scanning";
    await video.play().catch(() => {});
    tell(msg, "Hold the QR code up to the camera…");
    const look = async () => {
      if (!stream) return;
      try {
        const hit = (await det.detect(video)).map(c => c.rawValue).find(v => /shlink:\/|^shc:\//i.test(v || ""));
        if (hit) { stopScan(); text.value = hit; tell(msg, ""); inspect(); return; }
      } catch (e) {}
      setTimeout(look, 300);
    };
    look();
  });
  document.addEventListener("sources:group", stopScan);   // another section of the Sources page (#166)
})();
