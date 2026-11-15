/* Sanguity · import page: choose a profile (one you own or edit), then upload a zip, a folder or files to it.
   Each file is its own request (PUT, the body is the file), so a large export streams to the server with progress;
   the server unpacks zips. Once the upload is finished, window.sanguityImport.uploaded({profileId, uploadId})
   starts the import if that's there (#20); otherwise the page says importing comes next. */
"use strict";   // $, el and api come from util.js
const KINDS = ["zip", "folder", "files"];
const SKIP = /(^|\/)(\.DS_Store|Thumbs\.db|desktop\.ini)$/i;   // what file managers leave in folders: not records
const PARALLEL = 3;   // files sent at once
let ME, LIMITS = null, chosen = [], busy = false;

/* api() from util.js, returning the JSON answer, or throwing with the server's message */
async function call(path, method = "GET") {
  const r = await api(path, {method});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
  return j;
}
const say = (text, bad = false) => { const m = $("#msg"); m.textContent = text; m.classList.toggle("bad", bad); };
const size = n => n >= 2 ** 30 ? (n / 2 ** 30).toFixed(1) + " GB" : n >= 2 ** 20 ? (n / 2 ** 20).toFixed(1) + " MB"
  : n >= 1024 ? Math.round(n / 1024) + " KB" : n + " bytes";
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

$("#logout").onclick = async () => { try { await call("logout", "POST"); } catch (e) {} location.replace("login.html"); };

/* ---------------------------------------------------------------- what to upload: tabs */
function showKind(name, focus = false) {
  for (const k of KINDS) {
    const on = k === name, b = $("#tab-" + k);
    b.setAttribute("aria-selected", on); b.tabIndex = on ? 0 : -1;
    $("#panel-" + k).hidden = !on;
  }
  if (focus) $("#tab-" + name).focus();
}
$("#kindTabs").addEventListener("click", ev => { const b = ev.target.closest("[role=tab]"); if (b) showKind(b.id.slice(4)); });
$("#kindTabs").addEventListener("keydown", ev => {   // arrow keys move between tabs, as for any tab list
  const i = KINDS.indexOf(document.activeElement.id.slice(4));
  if (i < 0) return;
  const next = ev.key === "ArrowRight" ? KINDS[(i + 1) % KINDS.length] : ev.key === "ArrowLeft" ? KINDS[(i + KINDS.length - 1) % KINDS.length]
    : ev.key === "Home" ? KINDS[0] : ev.key === "End" ? KINDS[KINDS.length - 1] : null;
  if (next) { ev.preventDefault(); showKind(next, true); }
});

/* ---------------------------------------------------------------- choosing: pickers and dropping */
function choose(items) {
  if (busy) return;
  chosen = items.filter(it => !SKIP.test(it.path));
  say("");
  render();
}
$("#pickZip").onchange = e => choose([...e.target.files].map(f => ({file: f, path: f.name})));
$("#pickFolder").onchange = e => choose([...e.target.files].map(f => ({file: f, path: f.webkitRelativePath || f.name})));
$("#pickFiles").onchange = e => choose([...e.target.files].map(f => ({file: f, path: f.name})));

/* a dropped folder is walked with the entries API, keeping each file's path inside it */
const entryFile = entry => new Promise((ok, fail) => entry.file(ok, fail));
const readAll = async dir => {
  const reader = dir.createReader(), out = [];
  for (;;) {   // readEntries hands them over a batch at a time
    const batch = await new Promise((ok, fail) => reader.readEntries(ok, fail));
    if (!batch.length) return out;
    out.push(...batch);
  }
};
async function walk(entry, out) {
  if (entry.isFile) out.push({file: await entryFile(entry), path: entry.fullPath.replace(/^\/+/, "")});
  else if (entry.isDirectory) for (const e of await readAll(entry)) await walk(e, out);
}
const drop = $("#drop");
for (const t of ["dragenter", "dragover"]) drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", async e => {
  e.preventDefault(); drop.classList.remove("over");
  const entries = [...(e.dataTransfer.items || [])].map(i => i.webkitGetAsEntry ? i.webkitGetAsEntry() : null).filter(Boolean);
  if (!entries.length) { choose([...e.dataTransfer.files].map(f => ({file: f, path: f.name}))); return; }
  const out = [];
  try { for (const en of entries) await walk(en, out); } catch (err) { say("Couldn't read what was dropped: " + err.message, true); return; }
  choose(out);
});
$("#clear").onclick = () => { for (const id of ["#pickZip", "#pickFolder", "#pickFiles"]) $(id).value = ""; choose([]); };

/* the list, with what the server would refuse marked before anything is sent */
function problem() {
  if (!LIMITS) return "";
  const big = chosen.find(it => it.file.size > LIMITS.file_bytes);
  if (big) return `${big.path} is ${size(big.file.size)}: a file can be at most ${size(LIMITS.file_bytes)}.`;
  if (chosen.length > LIMITS.files) return `That's ${chosen.length} files: an upload can hold at most ${LIMITS.files}.`;
  const total = chosen.reduce((s, it) => s + it.file.size, 0);
  if (total > LIMITS.total_bytes) return `That's ${size(total)}: an upload can hold at most ${size(LIMITS.total_bytes)}.`;
  return "";
}
function render() {
  $("#chosen").hidden = !chosen.length;
  const total = chosen.reduce((s, it) => s + it.file.size, 0);
  $("#sum").textContent = `${plural(chosen.length, "file")}, ${size(total)}`;
  const shown = chosen.slice(0, 500);   // a folder of thousands: the first ones, and a count
  $("#list").replaceChildren(...shown.map(it => {
    it.bar = el("progress", {max: 1, value: 0, "aria-label": "Sent of " + it.path});
    it.state = el("span", {class: "muted"});
    return it.li = el("li", {}, el("span", {class: "path", text: it.path}), el("span", {class: "muted", text: size(it.file.size)}), it.bar, it.state);
  }), ...(chosen.length > shown.length ? [el("li", {class: "muted", text: `…and ${chosen.length - shown.length} more`})] : []));
  const why = problem();
  if (why) say(why, true);
  $("#go").disabled = busy || !chosen.length || !!why || !$("#profile").value;
}

/* ---------------------------------------------------------------- uploading */
function putFile(pid, uploadId, it, xhrs) {
  return new Promise((ok, fail) => {
    const x = new XMLHttpRequest();
    xhrs.add(x);
    x.open("PUT", `api/profiles/${pid}/uploads/${uploadId}/files?path=${encodeURIComponent(it.path)}`);
    x.setRequestHeader("X-Sanguity", "1");   // the API's cross-site check, as api() sends it
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = e => { if (it.bar && e.lengthComputable) it.bar.value = e.loaded / Math.max(e.total, 1); };
    x.onload = () => {
      xhrs.delete(x);
      if (x.status === 401) { location.replace("login.html"); return; }
      if (x.status === 200) { ok(); return; }
      let d; try { d = JSON.parse(x.responseText).detail; } catch (e) {}
      fail(new Error(typeof d === "string" ? d : `${it.path}: ${x.status} ${x.statusText}`));
    };
    x.onerror = () => { xhrs.delete(x); fail(new Error(`${it.path} didn't reach the server (the connection dropped).`)); };
    x.onabort = () => { xhrs.delete(x); fail(new Error("stopped")); };
    x.send(it.file);
  });
}
function setBusy(on) {
  busy = on;
  for (const id of ["#profile", "#pickZip", "#pickFolder", "#pickFiles", "#clear"]) $(id).disabled = on;
  $("#go").disabled = on || !chosen.length;
  $("#importer").setAttribute("aria-busy", on);
}
const leaving = e => { e.preventDefault(); e.returnValue = ""; };
$("#go").onclick = async () => {
  const pid = +$("#profile").value, items = chosen.slice(), xhrs = new Set();
  let uploadId = null, done = 0;
  setBusy(true); addEventListener("beforeunload", leaving);
  try {
    say("Starting the upload…");
    uploadId = (await call(`profiles/${pid}/uploads`, "POST")).id;
    let next = 0;
    const worker = async () => {
      while (next < items.length) {
        const it = items[next++];
        if (it.state) it.state.textContent = "sending";
        await putFile(pid, uploadId, it, xhrs);
        if (it.bar) it.bar.value = 1;
        if (it.state) it.state.textContent = "sent";
        say(`Sent ${done += 1} of ${items.length}…`);
      }
    };
    try { await Promise.all(Array.from({length: Math.min(PARALLEL, items.length)}, worker)); }
    catch (e) { for (const x of xhrs) x.abort(); throw e; }
    say(items.some(it => /\.zip$/i.test(it.path)) ? "Unpacking on the server…" : "Finishing…");
    const r = await call(`profiles/${pid}/uploads/${uploadId}/finish`, "POST");
    const sent = `Uploaded ${plural(items.length, "file")}` + (r.files !== items.length ? ` (${r.files} once unpacked)` : "") + ".";
    const hook = window.sanguityImport && window.sanguityImport.uploaded;
    if (typeof hook === "function") { say(sent); hook({profileId: pid, uploadId}); }
    else say(`${sent} Uploaded; importing comes next.`);
    chosen = [];
    $("#go").disabled = true;
  } catch (e) {
    if (uploadId != null) try { await call(`profiles/${pid}/uploads/${uploadId}`, "DELETE"); } catch (x) {}   // nothing half-sent is kept
    say("The upload didn't finish: " + e.message, true);
  } finally {
    removeEventListener("beforeunload", leaving);
    setBusy(false);
    $("#go").disabled = !chosen.length;
  }
};

/* ---------------------------------------------------------------- start */
(async () => {
  try { ME = await call("me"); } catch (e) { return; }
  if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
  if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
  $("#who").textContent = ME.user.username;
  { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
  const mine = ME.profiles.filter(p => p.role === "owner" || p.role === "editor");
  if (!mine.length) { $("#noProfiles").hidden = false; return; }
  const sel = $("#profile");
  sel.replaceChildren(...mine.map(p => el("option", {value: p.id, text: p.name})));
  let want; try { want = +(new URLSearchParams(location.search).get("profile") || localStorage.getItem("ht-profile")); } catch (e) {}
  if (mine.some(p => p.id === want)) sel.value = want;
  sel.onchange = render;
  $("#importer").hidden = false;
  try { LIMITS = await call("uploads/limits"); } catch (e) {}
  render();
})();
