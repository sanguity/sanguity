/* Sanguity · import history (#24): a section of the Sources page (#166) listing the profile's imports, newest first, with
   who, the importer, the upload's files and counts, and an Undo for owners and editors.
     GET  api/profiles/{pid}/imports              {can_undo, imports: [...]}
     POST api/profiles/{pid}/imports/{id}/undo    removes everything that import brought
   It follows the page's Profile choice, which lists every profile shared with you (sources-page.js); with none, a
   choice of its own. After an import is saved or undone anywhere on the page (import-jobs.js told, #508), the list is read again.
   Each import is #import-<id>: a record's Source link on the main page (sources.js, #100) opens
   sources.html?profile=<pid>#import-<id>, and that import is scrolled to and highlighted.
   Plain script in its own scope; $, el and api come from util.js, the counts' words and the Undo's question from import-jobs.js (importCounts, undoAsks). */
"use strict";
(function () {
  const ORIGIN = {upload: "Uploaded", manual: "Typed in", sync: "Synced", dataset: "Loaded from the command line", build: "From the setup build"};
  const when = t => fmtDT(t * 1000);   // dates.js
  let ME = null, PID = null, seq = 0;

  const section = el("section", {class: "card", id: "history", hidden: true, "aria-labelledby": "historyTitle"},
    el("h2", {id: "historyTitle", text: "Import history"}),
    el("p", {class: "hint", text: "Everything imported into this profile, newest first. Undoing an import removes the records, " +
      "files and watch data it brought, and nothing else."}),
    el("div", {class: "form inline", id: "historyPick", hidden: true}, el("label", {}, "Profile ", el("select", {id: "historyProfile"}))),
    el("div", {class: "row"}, el("span", {class: "msg", id: "historyMsg", role: "status"})),
    el("ul", {class: "hist", id: "historyList", "aria-label": "Imports"}),
    // rules that move a source's entries to another category, now and in its later imports (#237; the drawer makes them)
    el("div", {id: "kindRules", hidden: true}, el("h3", {text: "Category rules"}),
      el("p", {class: "hint", text: "Entries from a source that always go to another category, in every import from it, made with " +
        "Change category on an entry. An entry moved on its own stays where it was put."}),
      el("ul", {class: "hist", id: "kindRuleList", "aria-label": "Category rules"})));
  const CATEGORY = {visit: "Visits", contact: "Chart activity", procedure: "Procedures", imaging: "Imaging", study: "Reports & studies",
                    message: "Messages", appt_msg: "Appointment messages", auto_msg: "Automated messages", letter: "Letters", document: "Documents"};
  ($("main") || document.body).append(section);
  const say = (text, bad = false) => sayIn($("#historyMsg"), text, bad);   // shown and said (util.js, #209)

  async function call(path, method = "GET") {
    const r = await api(path, {method});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || `The server answered ${r.status}.`);
    return j;
  }

  const counts = e => sanguityImport.importCounts(e.counts);   // in the words the Undo asks in (import-jobs.js, #508)

  function item(e, canUndo) {
    const undone = e.state === "undone";
    const li = el("li", {class: undone ? "undone" : null, id: "import-" + e.id, tabindex: "-1", "data-import": e.id, "data-format": e.format},
      el("div", {class: "hd"}, el("b", {text: e.name || e.label}), e.name ? el("span", {class: "muted", text: `(${e.label})`}) : null,   // its own name (#38)
        el("span", {class: "muted", text: `${ORIGIN[e.origin] || e.origin} ${when(e.at)}` + (e.by ? ` · ${e.by}` : "")}),   // no name: typed in before it was kept (#325)
        undone ? el("span", {class: "tag", text: "undone"}) : null),
      e.files && e.files.length ? el("p", {class: "files muted", text: "Files: " + e.files.join(", ") +
        (e.file_count > e.files.length ? ` and ${(e.file_count - e.files.length).toLocaleString()} more` : "")}) : null,
      el("p", {class: "what", text: counts(e) + (e.range ? ` (${e.range.from === e.range.to ? recordDate(e.range.from) : recordDate(e.range.from) + " to " + recordDate(e.range.to)})` : "")}),   // dates.js (#317)
      undone ? el("p", {class: "muted", text: `Undone ${when(e.undone_at)} by ${e.undone_by}.`}) : null,
      !undone && e.note ? el("p", {class: "hint note", text: e.note}) : null);
    if (!undone && canUndo && e.can_undo) {
      const go = el("button", {class: "btn small undo", type: "button", text: "Undo this import…"});
      go.onclick = () => confirmUndo(e, go);
      li.append(el("div", {class: "row"}, go));
    }
    return li;
  }

  // asked first, in the app's dialog (ask(), util.js; #363), the red button its action and focus on Keep it; in the
  // words the toast's Undo asks in (import-jobs.js undoAsks, #504), the lab-name matches it forgets read as it's asked
  async function confirmUndo(e, opener) {
    const now = e.removal ? null : await call(`profiles/${PID}/imports`).catch(() => null);
    const matches = ((now || {}).imports || []).find(x => x.id === e.id)?.lab_matches ?? e.lab_matches;
    if (!await ask({title: e.removal ? "Put back what this took out?" : "Undo this import?",
      message: e.removal ? `What comes back: ${counts(e).toLowerCase()}.` :
        sanguityImport.undoAsks({...e, lab_matches: matches}) + (e.origin === "dataset" ? " What it replaced when it was loaded won't come back." : ""),
      action: e.removal ? "Put them back" : "Undo import", cancel: "Keep it", danger: true})) return;
    opener.disabled = true;
    say("Undoing…");
    try {
      await call(`profiles/${PID}/imports/${e.id}/undo`, "POST");
      say(e.removal ? "Undone: the records it took out are back." : `Undone: the ${e.label} import's records are gone from the profile.`);
      await sanguityImport.told(PID, e.id, true);   // the page told, as a toast's Undo tells it (#508): this list read again (below)
      const li = document.getElementById("import-" + e.id);   // drawn again, marked undone: focus on it, not lost with the button
      if (li && opener && !opener.isConnected) li.focus();
    } catch (err) {
      say("Couldn't undo it: " + err.message, true);
      opener.disabled = false;
    }
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
      : [emptyState("Nothing has been imported into this profile yet: each upload, record typed in and portal pull is listed here, with a way to undo it.",
          r.can_undo ? {href: "#upload", text: "Upload files"} : null, {tag: "li", cls: ""})]));   // #199
    highlight(true);
    rules(pid, mine, r.can_undo);
  }

  async function rules(pid, mine, canEdit) {
    const box = $("#kindRules");
    if (!canEdit) { box.hidden = true; return; }   // edits are listed to owners and editors
    let r;
    try { r = await call(`profiles/${pid}/edits`); } catch (err) { box.hidden = true; return; }
    if (mine !== seq) return;
    const list = (r.edits || []).filter(x => x.action === "kind_rule");
    box.hidden = !list.length;
    $("#kindRuleList").replaceChildren(...list.map(x => {
      const v = x.value || {}, n = x.ids.length;
      const del = el("button", {class: "btn small", type: "button", text: "Remove", "data-rule": x.id, onclick: async () => {
        del.disabled = true;
        try { await call(`profiles/${pid}/edits/${x.id}`, "DELETE"); say("Rule removed, with the changes made with it."); load(); }
        catch (err) { del.disabled = false; say("Couldn't remove the rule: " + err.message, true); }
      }});
      return el("li", {}, el("span", {},
        `${CATEGORY[v.from] || v.from} titled “${v.title}”${v.label ? " from " + v.label : ""} go to ${CATEGORY[v.k] || v.k}`,
        el("small", {class: "muted", text: ` · ${n ? `moves ${n.toLocaleString()} now` : "nothing to move now"}`})), " ", del);
    }));
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
    const page = $("#profile");
    if (page) {   // the page's Profile choice is the same list: follow it
      const follow = () => { if (page.value) choose(page.value); };
      page.addEventListener("change", follow);
      new MutationObserver(follow).observe(page, {childList: true});   // filled in by sources-page.js
      setTimeout(follow, 0);
    } else {   // on a page without one: a choice of its own, over every profile
      const sel = $("#historyProfile");
      sel.replaceChildren(...all.map(p => el("option", {value: p.id, text: p.role === "viewer" ? `${p.name} (view only)` : p.name})));
      let want; try { want = +(new URLSearchParams(location.search).get("profile") || localStorage.getItem("ht-profile")); } catch (e) {}
      if (all.some(p => p.id === want)) sel.value = want;
      $("#historyPick").hidden = false;
      sel.onchange = () => choose(sel.value);
      choose(sel.value);
    }
  })();

  // an import of this profile saved or undone, wherever it was (import-jobs.js told, #508: an upload's card, Waiting
  // for you's review, a toast's Undo, this list's own): the list read again
  document.addEventListener("sg:imports", ev => { if (ev.detail.pid === PID) ev.detail.wait(load()); });
})();
