/* Sanguity · uploading files (#19, #281): one drop target, dropTarget(opts), for files or a folder, chosen with its
   buttons or dropped (anywhere on the page: drop.js hands a drop to the page's target). Whatever is chosen starts at
   once, with no Upload button: the server recognises the format itself, a zip unpacked there. Each file is its own
   request (PUT, the body is the file), so a large export streams to the server with progress, three at a time; then
   window.sanguityImport.uploaded (import-jobs.js) imports it in the same card, through to its review and Save.

   The target is on Sources → Upload files (below), in the getting-started guide's step 2 (guide.js), and in the
   dialog a drop on any other page opens (drop.js).
   dropTarget({pid, canEdit, onDone, ids}) -> {el, take(items), zone}
     pid()      the profile the files go into; canEdit() whether you may (owner or editor)
     onDone(j)  after an import is saved (j, the job, done)
     ids        the pickers' ids, #pickFiles and #pickFolder (one target on a page)
     take(items) uploads [{file, path}], as a drop does */
"use strict";   // $, el, api, icon, sayIn and announce come from util.js
const dropTarget = (() => {
  const SKIP = /(^|\/)(\.DS_Store|Thumbs\.db|desktop\.ini)$/i;   // what file managers leave in folders: not records
  const PARALLEL = 3;   // files sent at once
  let limits = null, sending = 0;
  const getLimits = () => limits || (limits = api("uploads/limits").then(r => r.ok ? r.json() : null).catch(() => null));

  /* api() from util.js, returning the JSON answer, or throwing with the server's message */
  async function call(path, method = "GET") {
    const r = await api(path, {method});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
    return j;
  }
  const size = n => n >= 2 ** 30 ? (n / 2 ** 30).toFixed(1) + " GB" : n >= 2 ** 20 ? (n / 2 ** 20).toFixed(1) + " MB"
    : n >= 1024 ? Math.round(n / 1024) + " KB" : n + " bytes";
  const plural = (n, word) => `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;
  const zipped = it => /\.zip$/i.test(it.path);   // what the server unpacks
  const leaving = e =>{ if (!sgLeaving) { e.preventDefault(); e.returnValue = ""; } };   // while files are being sent

  /* what the server would refuse, said before anything is sent */
  function problem(items, L) {
    if (!L) return "";
    const big = items.find(it => it.file.size > L.file_bytes);
    if (big) return `${big.path} is ${size(big.file.size)}: a file can be at most ${size(L.file_bytes)}.`;
    if (items.length > L.files) return `That's ${items.length.toLocaleString("en-US")} files: an upload can hold at most ${L.files.toLocaleString("en-US")}.`;
    const total = items.reduce((s, it) => s + it.file.size, 0);
    if (total > L.total_bytes) return `That's ${size(total)}: an upload can hold at most ${size(L.total_bytes)}.`;
    return "";
  }
  /* its name on the card: the file, the folder, or how many */
  function named(items) {
    const top = items[0].path.split("/");
    if (items.length === 1) return top.at(-1);
    if (top.length > 1 && items.every(it => it.path.startsWith(top[0] + "/"))) return top[0];
    return plural(items.length, "file");
  }

  function putFile(pid, uploadId, it, xhrs, moved) {
    return new Promise((ok, fail) => {
      const x = new XMLHttpRequest();
      xhrs.add(x);
      x.open("PUT", `api/profiles/${pid}/uploads/${uploadId}/files?path=${encodeURIComponent(it.path)}`);
      x.setRequestHeader("X-Sanguity", "1");   // the API's cross-site check, as api() sends it
      x.setRequestHeader("Content-Type", "application/octet-stream");
      x.upload.onprogress = e => { if (e.lengthComputable) moved(it, e.loaded); };
      x.onload = () => {
        xhrs.delete(x);
        if (x.status === 401) { location.replace("login.html"); return; }
        if (x.status === 200) { moved(it, it.file.size); ok(); return; }
        let d; try { d = JSON.parse(x.responseText).detail; } catch (e) {}
        fail(new Error(typeof d === "string" ? d : `${it.path}: ${x.status} ${x.statusText}`));
      };
      x.onerror = () => { xhrs.delete(x); fail(new Error(`${it.path} didn't reach the server (the connection dropped).`)); };
      x.onabort = () => { xhrs.delete(x); fail(new Error("stopped")); };
      x.send(it.file);
    });
  }

  /* send the files into a new upload, in the card f, then import it there; -> the job, or null */
  async function send(pid, items, f) {
    const xhrs = new Set(), total = items.reduce((s, it) => s + it.file.size, 0) || 1, sent = new Map();
    let uploadId = null, done = 0;
    sending++;
    if (sending === 1) addEventListener("beforeunload", leaving);
    const moved = (it, n) => {
      sent.set(it, n);
      const all = [...sent.values()].reduce((s, v) => s + v, 0);
      f.progress(all / total);
      f.text(`Sent ${size(all)} of ${size(total)}` + (items.length > 1 ? ` · ${done.toLocaleString("en-US")} of ${plural(items.length, "file")}` : ""));
    };
    try {
      f.step("send", items.length > 1 ? `Sending ${plural(items.length, "file")}…` : "Sending…");
      f.progress(0);
      uploadId = (await call(`profiles/${pid}/uploads`, "POST")).id;
      let next = 0;
      const worker = async () => {
        while (next < items.length) { const it = items[next++]; await putFile(pid, uploadId, it, xhrs, moved); done++; moved(it, it.file.size); }
      };
      try { await Promise.all(Array.from({length: Math.min(PARALLEL, items.length)}, worker)); }
      catch (e) { for (const x of xhrs) x.abort(); throw e; }
      // only a zip is unpacked (#497): a file or a folder has no stage of its own here (#489)
      if (items.some(zipped)) f.step("unpack", "Unpacking on the server…");
      else f.text(items.length > 1 ? "Checking the files on the server…" : "Checking the file on the server…");
      f.progress(null);
      const r = await call(`profiles/${pid}/uploads/${uploadId}/finish`, "POST");
      if (r.files !== items.length) f.note(`${plural(r.files, "file")} once unpacked`);
    } catch (e) {
      if (uploadId != null) try { await call(`profiles/${pid}/uploads/${uploadId}`, "DELETE"); } catch (x) {}   // nothing half-sent is kept
      f.end("failed");
      f.body.replaceChildren(el("p", {}, el("b", {text: "Not uploaded. "}), e.message));
      if (!f.card.hidden) announce("Not uploaded: " + e.message, {error: true});   // (one put away says nothing, #497)
      return null;
    } finally {
      if (--sending === 0) removeEventListener("beforeunload", leaving);
    }
    return window.sanguityImport.uploaded({profileId: pid, uploadId, frame: f, title: f.title});
  }

  return function dropTarget(opts) {
    const files = el("input", {type: "file", multiple: "", id: opts.ids ? "pickFiles" : null});
    const folder = el("input", {type: "file", multiple: "", webkitdirectory: "", id: opts.ids ? "pickFolder" : null});
    // a folder's picker where there is one: not on a phone or tablet, whose browsers offer files only
    const folders = "webkitdirectory" in HTMLInputElement.prototype && !matchMedia("(pointer: coarse)").matches;
    const msg = el("p", {class: "msg up-msg"});
    const zone = el("div", {class: "up-drop needs-server"},
      el("span", {class: "up-ico", "aria-hidden": "true"}, icon("upload")),
      el("p", {class: "up-say"}, folders ? "Drop files or a folder here, or" : "Choose the files to upload:"),
      el("div", {class: "up-btns"}, el("label", {class: "btn primary pick"}, "Choose files", files),
        folders ? el("label", {class: "btn pick"}, "Choose a folder", folder) : null),
      el("p", {class: "up-what", text: "A zip as it came, a folder or single files. Uploading starts at once; nothing is saved until you've seen what's in it."}));
    const jobs = el("div", {class: "up-jobs"});
    const box = el("div", {class: "up"}, zone, msg, jobs);
    const pick = (input, pathOf) => () => { const got = [...input.files].map(f => ({file: f, path: pathOf(f)})); input.value = ""; take(got); };
    files.onchange = pick(files, f => f.name);
    folder.onchange = pick(folder, f => f.webkitRelativePath || f.name);

    async function take(items) {
      items = items.filter(it => !SKIP.test(it.path));
      const say = (t, bad = true) => sayIn(msg, t, bad);
      if (!items.length) return say("Nothing to upload: that held no files.");
      if (typeof OFFLINE !== "undefined" && OFFLINE.on) return say("You're offline. Uploading needs the server.");
      if (!opts.canEdit()) return say("You can only view this profile: its owners and editors upload to it.");
      const why = problem(items, await getLimits());
      if (why) return say(why);
      say("");
      const total = items.reduce((s, it) => s + it.file.size, 0), pid = opts.pid();
      // nothing to unpack but a zip (#489, #497: a folder or several files neither)
      const f = window.sanguityImport.frame({container: jobs, title: named(items), pid,
        steps: items.some(zipped) ? null : ["send", "find", "review", "saved"], size: (items.length > 1 ? plural(items.length, "file") + " · " : "") + size(total)});
      const j = await send(pid, items, f);
      if (j && j.state === "done" && opts.onDone) opts.onDone(j);
      return j;
    }
    return {el: box, take, zone};
  };
})();

/* ---------------------------------------------------------------- Sources → Upload files */
/* the page signs in and fills the Profile choice (sources-page.js); the Sources sidebar lists this section, typing in
   and pasting a link only for the profile's owners and editors (#401). A drop anywhere on the page comes here, this
   section shown first (drop.js) */
if (document.getElementById("upDrop")) {
  const t = dropTarget({pid: () => SgSources.pid(), canEdit: () => SgSources.canEdit(), ids: true});
  $("#upDrop").replaceWith(t.el);
  if (typeof SgDrop !== "undefined") SgDrop.here(t, () => { if ($("#importer").hidden || !$("#importer").checkVisibility()) SgSources.show("upload"); });
  (async () => {
    if (!await SgSources.ready) return;
    for (const id of ["#importer", "#typeIn", "#healthLink"]) $(id).hidden = false;   // this page's tabs once (#166)
    /* another profile chosen (#490): its cards put away, so no Add saves into a profile the page no longer names. One
       over or in review goes (a review waits in its own profile's Waiting for you); one still on its way is hidden
       until its profile is chosen again */
    $("#profile").addEventListener("change", () => {
      const pid = String(SgSources.pid());
      for (const c of t.el.querySelectorAll(".job[data-pid]")) {
        if (c.dataset.pid === pid) c.hidden = false;
        else if ("settled" in c.dataset || c.dataset.step === "review") c.remove();
        else c.hidden = true;
      }
    });
  })();
}
