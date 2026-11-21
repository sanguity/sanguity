/* Sanguity · drop files anywhere (#281): on every page with the top bar (after nav.js). Files or a folder dragged
   over the page show a veil saying where they'd go: the profile shown, for its owners and editors (someone who can
   only view it, or a page offline, is told why not, and the drop does nothing). Dropped, they upload at once:
   into the page's own drop target, where it has one (Sources → Upload files, which it shows first; the guide's step
   2), else into one in a dialog over the page, its scripts (import-jobs.js, import.js) loaded then. The dialog's
   import goes through to its review there; on the records page, what it saved is drawn at once.
   Nothing dropped is kept anywhere but on its way to the server. A drag that isn't files (text, a link, a tab being
   moved in Settings) is left alone; files dropped while another modal dialog is open are kept from the browser, which
   would open them in place of the page, and go nowhere but a file field under them (#497). Files dropped on a form's own
   file field (or its label: a letter typed in, a journal entry's photos) go into that field, as its picker would put
   them, not into an upload (#489); the veil makes way while they're over it.
   SgDrop.here(target, show): the page's own target (dropTarget, import.js) and how to bring it into view. */
"use strict";
const SgDrop = (() => {
  let here = null, depth = 0, dlg = null;
  const veil = el("div", {class: "drop-veil", popover: "manual", "aria-hidden": "true"});
  const files = e => [...(e.dataTransfer?.types || [])].includes("Files");
  const away = () => typeof OFFLINE !== "undefined" && OFFLINE.on;
  const other = () => [...document.querySelectorAll("dialog:modal")].some(d => d !== dlg);
  // a form's own file field under the pointer, or the one its label is for: not a drop target's pickers (import.js)
  const field = e => {
    const t = e.target instanceof Element ? e.target : null, f = t?.closest("input[type=file]") || t?.closest("label")?.control;
    return f && f.type === "file" && !f.disabled && !f.closest(".up") ? f : null;
  };
  function fill(f, dropped) {
    const dt = new DataTransfer();
    for (const file of f.multiple ? dropped : dropped.slice(0, 1)) dt.items.add(file);
    f.files = dt.files;
    f.dispatchEvent(new Event("input", {bubbles: true}));
    f.dispatchEvent(new Event("change", {bubbles: true}));
  }
  function can() {
    const p = SgNav.profile();
    return !p ? [false, "Open a profile to upload files to it."]
      : away() ? [false, "You're offline. Uploading needs the server."]
      : p.role !== "owner" && p.role !== "editor" ? [false, `You can only view ${p.name}: its owners and editors upload to it.`]
      : [true, `Drop to upload to ${p.name}`];
  }
  function show() {
    const [yes, words] = can();
    veil.classList.toggle("no", !yes);
    veil.replaceChildren(el("div", {class: "drop-veil-box"}, icon(yes ? "upload" : "x"), el("p", {text: words})));
    if (!veil.isConnected) document.body.append(veil);
    if (!veil.matches(":popover-open")) veil.showPopover();
  }
  const hide = () => { depth = 0; if (veil.matches(":popover-open")) veil.hidePopover(); };

  // a file let go anywhere comes here, never opened by the browser in place of the page, while another modal is
  // open too (#497): then it goes only into a file field under it, and nothing else is done
  addEventListener("dragenter", e => {
    if (!files(e)) return;
    e.preventDefault();
    if (other()) return;
    if (depth++ === 0 && !field(e)) show();
  });
  addEventListener("dragover", e => {
    if (!files(e)) return;
    e.preventDefault();
    const f = field(e);
    if (other()) { e.dataTransfer.dropEffect = f ? "copy" : "none"; return; }
    if (f) { if (veil.matches(":popover-open")) veil.hidePopover(); }   // (out of the way of the field)
    else if (depth && !veil.matches(":popover-open")) show();
    e.dataTransfer.dropEffect = f || can()[0] ? "copy" : "none";
  });
  addEventListener("dragleave", e => { if (files(e) && depth > 0 && --depth === 0) hide(); });
  addEventListener("dragend", hide);
  addEventListener("mousemove", () => { if (depth) hide(); });   // (no mouse moves during a drag: one that ended unseen)
  addEventListener("drop", e => {
    if (!files(e)) return;
    e.preventDefault();
    hide();
    const f = field(e);
    if (f) { fill(f, [...e.dataTransfer.files]); return; }
    if (other()) return;
    const [yes, words] = can();
    if (!yes) { toast(words); return; }
    // the entries now, while the drop's data can be read; their files after
    const entries = [...(e.dataTransfer.items || [])].map(i => i.webkitGetAsEntry ? i.webkitGetAsEntry() : null).filter(Boolean);
    const loose = [...e.dataTransfer.files].map(f => ({file: f, path: f.name}));
    (entries.length ? walkAll(entries) : Promise.resolve(loose)).then(deliver, err => toast("Couldn't read what was dropped: " + err.message));
  });

  /* a dropped folder is walked with the entries API, keeping each file's path inside it */
  const entryFile = entry => new Promise((ok, fail) => entry.file(ok, fail));
  async function readAll(dir) {
    const reader = dir.createReader(), out = [];
    for (;;) {   // readEntries hands them over a batch at a time
      const batch = await new Promise((ok, fail) => reader.readEntries(ok, fail));
      if (!batch.length) return out;
      out.push(...batch);
    }
  }
  async function walk(entry, out) {
    if (entry.isFile) out.push({file: await entryFile(entry), path: entry.fullPath.replace(/^\/+/, "")});
    else if (entry.isDirectory) for (const e of await readAll(entry)) await walk(e, out);
  }
  async function walkAll(entries) { const out = []; for (const en of entries) await walk(en, out); return out; }

  /* the files to the page's target, else to the dialog's */
  async function deliver(items) {
    if (here) {
      here.show?.();
      here.target.zone.scrollIntoView({block: "nearest", behavior: smoothly()});
      return here.target.take(items);
    }
    try { for (const s of ["js/import-jobs.js", "js/import.js"]) await script(s); }
    catch (err) { toast("Couldn't open the upload: " + err.message); return; }
    open().take(items);
  }
  const script = src => typeof dropTarget !== "undefined" && src === "js/import.js" || typeof sanguityImport !== "undefined" && src === "js/import-jobs.js"
    ? Promise.resolve() : new Promise((ok, fail) => document.head.append(el("script", {src, onload: ok, onerror: () => fail(new Error("the page's files didn't load"))})));

  /* the dialog: one per profile, its cards kept while the page is open (another drop adds to them) */
  function open() {
    const p = SgNav.profile();
    if (!dlg || dlg.sgPid !== p.id) {
      dlg?.remove();
      const t = dropTarget({pid: () => p.id, canEdit: () => true, onDone: () => saved(p.id)});
      dlg = el("dialog", {class: "up-dlg", "aria-labelledby": "upDlgT"},   // (a heading and ✕ as a sheet's, nav.js phoneSheet)
        el("div", {class: "sheet-hd", "data-grab": ""}, el("h2", {id: "upDlgT", text: `Upload to ${p.name}`}),
          el("button", {class: "btn quiet sheet-x", type: "button", "aria-label": "Close", onclick: () => dlg.close()}, icon("x"))),
        el("div", {class: "sheet-body"}, t.el));
      dlg.sgPid = p.id; dlg.sgTake = t.take;
      document.body.append(dlg);
    }
    openDialog(dlg, {as: matchMedia("(max-width:700px)").matches ? "sheet" : "center"});
    return {take: dlg.sgTake};
  }
  /* a link to a tab of this page (the card's "See them on the timeline", its toast's Open Labs) only changes the
     address's hash: the dialog closes, so the tab isn't left under it (#497). "/" and "/index.html" are one page
     (#500: sign-in lands on "/", the links name index.html), as is an address without ?profile= and one naming the
     profile shown: the link goes to the hash alone, so nothing loads again, the dialog closed or not (a file may be
     sending in it, its cards kept) */
  const page = u => {
    const q = new URLSearchParams(u.search);
    if (q.get("profile") === String(SgNav.profile()?.id)) q.delete("profile");
    return u.origin + u.pathname.replace(/\/index\.html$/, "/") + "?" + q;
  };
  addEventListener("click", e => {
    const a = e.target instanceof Element ? e.target.closest("a[href]") : null;
    if (!dlg || !a || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const to = new URL(a.href, location.href);
    if (!to.hash || page(to) !== page(location)) return;
    if (to.pathname + to.search !== location.pathname + location.search) { e.preventDefault(); location.hash = to.hash; }
    dlg.close();
  });
  /* an import saved from the dialog on the records page: its records drawn again in place (store.js), or, for a
     profile that had none, the page loaded again once the dialog closes */
  async function saved(pid) {
    if (typeof storeRecords !== "function" || typeof PROFILE === "undefined" || !PROFILE || PROFILE.id !== pid) return;
    if (typeof DATA === "undefined" || !DATA) { dlg.addEventListener("close", () => location.reload(), {once: true}); return; }
    const r = await api(`profiles/${pid}/data`).catch(() => null);
    if (r && r.ok) storeRecords(await r.json());
  }

  return {here(target, show) { here = {target, show}; }};
})();
