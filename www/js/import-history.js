/* Sanguity · import history (#24): a section of the import page listing the profile's imports, newest first, with
   who, the importer, the upload's files and counts, and an Undo for owners and editors.
     GET  api/profiles/{pid}/imports              {can_undo, imports: [...]}
     POST api/profiles/{pid}/imports/{id}/undo    removes everything that import brought
   It follows the page's Profile choice; someone who only views a profile gets a choice of their own here (the
   upload form only lists profiles they can import into). After an import finishes through window.sanguityImport
   (import-jobs.js), the list is read again. Each import is #import-<id>: a record's Source link on the main page
   (sources.js, #100) opens import.html?profile=<pid>#import-<id>, and that import is scrolled to and highlighted.
   Plain script in its own scope; $, el and api come from util.js. */
"use strict";
(function () {
  const KIND = {visit: ["visit", "visits"], lab: ["lab draw", "lab draws"], imaging: ["imaging study", "imaging studies"],
                procedure: ["procedure", "procedures"], study: ["report", "reports"], message: ["message", "messages"],
                letter: ["letter", "letters"], document: ["document", "documents"], stay: ["hospital stay", "hospital stays"],
                med: ["medication", "medications"], rx: ["pharmacy fill", "pharmacy fills"], claim: ["claim", "claims"]};
  const ORIGIN = {upload: "Uploaded", dataset: "Loaded from the command line", build: "From the setup build"};
  const n = (k, [one, many]) => `${k.toLocaleString()} ${k === 1 ? one : many}`;
  const when = t => new Date(t * 1000).toLocaleString(undefined, {year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"});
  let ME = null, PID = null, seq = 0;

  const section = el("section", {class: "card", id: "history", hidden: true, "aria-labelledby": "historyTitle"},
    el("h2", {id: "historyTitle", text: "Import history"}),
    el("p", {class: "hint", text: "Everything imported into this profile, newest first. Undoing an import removes the records, " +
      "files and watch data it brought, and nothing else."}),
    el("div", {class: "form inline", id: "historyPick", hidden: true}, el("label", {}, "Profile ", el("select", {id: "historyProfile"}))),
    el("div", {class: "row"}, el("span", {class: "msg", id: "historyMsg", role: "status"})),
    el("ul", {class: "hist", id: "historyList", "aria-label": "Imports"}));
  ($("main") || document.body).append(section);
  const say = (text, bad = false) => { const m = $("#historyMsg"); m.textContent = text; m.classList.toggle("bad", bad); };

  async function call(path, method = "GET") {
    const r = await api(path, {method});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || `The server answered ${r.status}.`);
    return j;
  }

  function counts(e) {
    const c = e.counts || {}, out = [];
    const kinds = Object.entries(c.kinds || {}).filter(([k]) => k !== "labitem").sort((a, b) => b[1] - a[1]);
    if (kinds.length) out.push(...kinds.map(([k, v]) => n(v, KIND[k] || [k, k])));
    else if (c.events) out.push(n(c.events, ["event", "events"]));
    if (c.results) out.push(n(c.results, ["lab result", "lab results"]) + " in " + n(c.series || 0, ["test", "tests"]));
    if (c.documents) out.push(n(c.documents, ["attached file", "attached files"]));
    if (c.watch_days) out.push(n(c.watch_days, ["day", "days"]) + " of watch data");
    if (c.workouts) out.push(n(c.workouts, ["workout", "workouts"]));
    return out.length ? out.join(", ") : "No records";
  }

  function item(e, canUndo) {
    const undone = e.state === "undone";
    const li = el("li", {class: undone ? "undone" : null, id: "import-" + e.id, tabindex: "-1", "data-import": e.id, "data-format": e.format},
      el("div", {class: "hd"}, el("b", {text: e.label}), el("span", {class: "muted", text: `${ORIGIN[e.origin] || e.origin} ${when(e.at)} · ${e.by}`}),
        undone ? el("span", {class: "tag", text: "undone"}) : null),
      e.files && e.files.length ? el("p", {class: "files muted", text: "Files: " + e.files.join(", ") +
        (e.file_count > e.files.length ? ` and ${(e.file_count - e.files.length).toLocaleString()} more` : "")}) : null,
      el("p", {class: "what", text: counts(e) + (e.range ? ` (${e.range.from === e.range.to ? e.range.from : e.range.from + " to " + e.range.to})` : "")}),
      undone ? el("p", {class: "muted", text: `Undone ${when(e.undone_at)} by ${e.undone_by}.`}) : null,
      !undone && e.note ? el("p", {class: "hint note", text: e.note}) : null);
    if (!undone && canUndo && e.can_undo) {
      const go = el("button", {class: "btn small undo", type: "button", text: "Undo this import…"});
      go.onclick = () => confirmUndo(li, e, go);
      li.append(el("div", {class: "row"}, go));
    }
    return li;
  }

  function confirmUndo(li, e, opener) {
    if (li.querySelector(".confirm")) return;
    opener.disabled = true;
    const yes = el("button", {class: "btn small danger", type: "button", text: "Undo import"});
    const no = el("button", {class: "btn small", type: "button", text: "Keep it"});
    const box = el("div", {class: "confirm", role: "group", "aria-label": "Confirm undo"},
      el("p", {text: `Remove everything this ${e.label} import brought: ${counts(e).toLowerCase()}` +
        (e.upload != null ? ", and the files uploaded for it" : "") + ". This can't be reversed; you can import the files again." +
        (e.origin === "dataset" ? " What it replaced when it was loaded won't come back." : "")}),
      el("div", {class: "row"}, yes, no));
    no.onclick = () => { box.remove(); opener.disabled = false; opener.focus(); };
    yes.onclick = async () => {
      yes.disabled = no.disabled = true;
      say("Undoing…");
      try {
        await call(`profiles/${PID}/imports/${e.id}/undo`, "POST");
        say(`Undone: the ${e.label} import's records are gone from the profile.`);
        await load();
      } catch (err) {
        say("Couldn't undo it: " + err.message, true);
        box.remove(); opener.disabled = false;
      }
    };
    li.append(box);
    yes.focus();
  }

  async function load() {
    const pid = PID, mine = ++seq;
    if (!pid) { section.hidden = true; return; }
    let r;
    try { r = await call(`profiles/${pid}/imports`); }
    catch (err) { if (mine === seq) { section.hidden = false; say("Couldn't load the import history: " + err.message, true); } return; }
    if (mine !== seq) return;   // another profile was chosen meanwhile
    section.hidden = false;
    const list = r.imports || [];
    $("#historyList").replaceChildren(...(list.length ? list.map(e => item(e, r.can_undo))
      : [el("li", {class: "muted", text: "Nothing has been imported into this profile yet."})]));
    highlight(true);
  }

  // the import a record's Source link names (#import-<id>): scrolled to, highlighted and focused, once per link followed
  let shown = null;
  function highlight(fromLoad) {
    const m = /^#import-(\d+)$/.exec(location.hash);
    document.querySelectorAll("#historyList > li.hl").forEach(li => li.classList.remove("hl"));
    const li = m && document.getElementById("import-" + m[1]);
    if (!li) return;
    li.classList.add("hl");
    if (fromLoad && shown === location.hash) return;   // a reload of the list after an undo: leave the focus where it is
    shown = location.hash;
    li.scrollIntoView({block: "center"});
    li.focus({preventScroll: true});
  }
  addEventListener("hashchange", () => highlight(false));

  function choose(pid) {
    pid = +pid || null;
    if (pid === PID) return;
    PID = pid; say(""); load();
  }

  (async () => {
    try { ME = await call("me"); } catch (e) { return; }
    const all = ME.profiles || [];
    if (!all.length) return;
    const editable = all.filter(p => p.role === "owner" || p.role === "editor");
    const page = $("#profile");
    if (editable.length === all.length && page) {   // the upload form's Profile choice is the same list: follow it
      const follow = () => { if (page.value) choose(page.value); };
      page.addEventListener("change", follow);
      new MutationObserver(follow).observe(page, {childList: true});   // filled in by import.js
      setTimeout(follow, 0);
    } else {   // some are view-only: a choice of its own, over every profile, kept in step with the form's
      const sel = $("#historyProfile");
      sel.replaceChildren(...all.map(p => el("option", {value: p.id, text: p.role === "viewer" ? `${p.name} (view only)` : p.name})));
      let want; try { want = +(new URLSearchParams(location.search).get("profile") || localStorage.getItem("ht-profile")); } catch (e) {}
      if (all.some(p => p.id === want)) sel.value = want;
      $("#historyPick").hidden = false;
      sel.onchange = () => choose(sel.value);
      if (page) page.addEventListener("change", () => { sel.value = page.value; choose(sel.value); });
      choose(sel.value);
    }
  })();

  // after an import finishes (import-jobs.js resolves with the job), read the list again
  const hook = window.sanguityImport;
  if (hook && typeof hook.uploaded === "function") {
    const uploaded = hook.uploaded;
    hook.uploaded = async opts => { try { return await uploaded(opts); } finally { if (opts && +opts.profileId === PID) load(); } };
  }
})();
