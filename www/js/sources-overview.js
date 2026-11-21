/* Sanguity · Sources → Overview (#166, #283): the hub of the Sources page.
   - Waiting for you (#waiting), for the profile's owners and editors: each import left at its preview, a portal's first
     pull among them (nav.js sgWaiting), the latest few and then "Show N more" (#490), each saying what it would bring
     as the server reads it now, with Review (its preview, opened here: import-jobs.js) and Cancel import…; and
     each portal that needs you (access ended, or stopped syncing), with the way to it. A preview left open when a tab
     was closed is found again here. Add's "Waiting for you" (nav.js) opens it, and is told its count.
   - Where records come from (#srcOverview): one card per way in, each with how it's doing ("Connected · synced 2 h
     ago", "Not set up") and its last activity, its title the link to its section; on a phone, where the sidebar gives
     way to them (app.css), the way into each section. A profile nothing has come into yet has an empty state with a
     picture above them; someone who can only view the profile sees the ways that brought it records, and the history.
   Read again when the profile changes, each time the Overview is shown, and once an import is saved or undone while
   it's shown (#513, #521).
     GET api/profiles/{pid}/connections         what each connection is doing, and when it last pulled (connect.js's)
     GET api/profiles/{pid}/imports             every import, a sync's naming its connection (import-history.js's)
     GET api/profiles/{pid}/watch/receiver      the phone's setup and its last upload (owners and editors)
     GET api/profiles/{pid}/jobs?state=preview  the imports left at their preview (owners and editors)
     POST api/profiles/{pid}/jobs/{id}/discard  Cancel import… */
"use strict";   // $, el and api come from util.js
(() => {
  const when = fmtWhen;   // dates.js
  const box = $("#srcOverview"), wait = $("#waiting"), waitList = $("#srcWait"), review = $("#srcReview");
  let seq = 0;

  async function get(path) {
    const r = await api(path);
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
    return j;
  }
  /* how long ago, for a status line ("synced 2 h ago"); a date once it's a week or more */
  function ago(s) {
    const d = Date.now() / 1000 - s;
    return d < 60 ? "just now" : d < 3600 ? `${Math.floor(d / 60)} min ago` : d < 86400 ? `${Math.floor(d / 3600)} h ago`
      : d < 7 * 86400 ? `${Math.floor(d / 86400)} day${d < 2 * 86400 ? "" : "s"} ago` : fmtD(s * 1000);
  }
  const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

  /* ---------------------------------------------------------------- Waiting for you */
  /* an import's preview, opened here: the card it has already (a first pull's, connect.js, or one left open in Upload
     files) brought here and drawn again as the server reads it now (#490: the same file may have been saved since), or
     a new one following it, titled by its format as its row is; focus on it. Once it's saved or cancelled the list is
     read again; the card stays, saying so, until another section is shown (sources-page.js) */
  function open(job) {
    let card = document.querySelector(`.import-job[data-job="${job.id}"]:not([data-settled])`);
    if (card) { review.append(card.closest(".job") || card); window.sanguityImport.fresh(card); }   // its whole card (#281)
    else {
      window.sanguityImport.follow({profileId: SgSources.pid(), jobId: job.id, container: review, title: job.label || "An import",   // its format, as its row (#490)
                                    text: "Opening its preview…"}).then(() => load());
      card = review.lastElementChild.querySelector(".import-job");   // the job's own part of its card (#281)
    }
    card.tabIndex = -1;
    card.focus();
    load();
  }
  async function cancel(job, conn) {
    if (!await ask({title: "Cancel this import?", danger: true, action: "Cancel import", cancel: "Keep it",
                    message: conn ? `Nothing from ${conn.source}'s first pull is saved. You can pull again from Portals and health plans.`
                                  : "Nothing from it is saved, and the files uploaded for it are removed. You can upload them again."})) return;
    let r;
    try { r = await api(`profiles/${SgSources.pid()}/jobs/${job.id}/discard`, {method: "POST"}); }
    catch (e) { toast("Not cancelled: the server can't be reached."); return; }
    if (!r.ok) { const e = await r.json().catch(() => ({})); toast(`Not cancelled: ${typeof e.detail === "string" ? e.detail : r.status}`); return; }
    document.querySelector(`.job[data-job="${job.id}"]`)?.remove();   // its card elsewhere, with a Save that can't work now
    toast("Cancelled: nothing from that import was saved.");
    await load();
    // focus on what's there now: the next thing waiting, else the heading
    const next = waitList.querySelector("button, a");
    if (next) next.focus();
    else { const h = $("#groupTitle"); h.tabIndex = -1; h.focus(); }
  }
  function waitRow(w) {
    const {job, conn} = w, r = job && job.range;
    if (job) {
      const said = [job.new === false && !job.stale ? "All in the profile already" : "Ready to review",   // (stale: read before the profile changed, #490)
        r ? (r.from === r.to ? `records from ${fmtD(dayTs(r.from))}` : `records from ${fmtD(dayTs(r.from))} to ${fmtD(dayTs(r.to))}`) : null,
        `${conn ? "pulled" : "uploaded"} ${ago(job.created)}`].filter(Boolean).join(" · ");
      return el("li", {class: "src-wait-row", "data-job": job.id}, icon(conn ? "plug" : "upload"),
        el("div", {class: "src-wait-t"}, el("b", {text: conn ? `${conn.source}: its first pull` : job.label || "An import"}), el("p", {text: said})),
        el("div", {class: "src-wait-acts"},
          el("button", {class: "btn primary small needs-server", type: "button", "data-key": "review" + job.id, onclick: () => open(job)}, "Review"),
          el("button", {class: "btn small needs-server", type: "button", "data-key": "cancel" + job.id, onclick: () => cancel(job, conn)}, "Cancel import…")));
    }
    const ended = conn.state === "needs reconnect";
    return el("li", {class: "src-wait-row bad"}, icon("triangle-alert", {label: "Warning:"}),
      el("div", {class: "src-wait-t"}, el("b", {text: ended ? `${conn.source}: access ended` : conn.source}),
        el("p", {text: ended ? "Sign in again to keep it up to date." : conn.warning.text})),
      el("div", {class: "src-wait-acts"}, el("a", {class: "btn small", href: "#conn-" + conn.id, text: ended ? "Sign in again" : "Fix it"})));
  }
  /* the latest few (#490: a dozen left open would push the ways in far down), then "Show N more" for the rest, which
     stay shown until the profile changes */
  const FEW = 5;
  let all = false;
  function drawWaiting(items) {
    const shown = items.filter(w => !w.job || !review.querySelector(`.import-job[data-job="${w.job.id}"]:not([data-settled])`));   // (one open below)
    const had = waitList.contains(document.activeElement) && document.activeElement.dataset.key;
    const few = all || shown.length <= FEW + 1 ? shown : shown.slice(0, FEW);
    waitList.replaceChildren(...few.map(waitRow));
    if (few.length < shown.length) {
      const n = shown.length - few.length;
      waitList.append(el("li", {class: "src-wait-more"}, el("button", {class: "btn quiet small", type: "button", "data-key": "more", onclick: () => {
        all = true;
        drawWaiting(items);
        waitList.children[FEW]?.querySelector("button, a")?.focus();   // the first of the rest
      }}, `Show ${n.toLocaleString()} more`)));
    }
    if (!shown.length && review.children.length) waitList.append(el("li", {class: "src-wait-none", text: "Nothing else is waiting for you."}));
    if (had) waitList.querySelector(`[data-key="${had}"]`)?.focus();
    wait.hidden = !shown.length && !review.children.length;
  }

  /* ---------------------------------------------------------------- the ways in */
  /* one card: its icon and title (the link to its section, when it can be opened), how it's doing (ok, off or bad, said
     in words), its last activity, and what else it lists */
  function way({id, title, ico, state, status, last, more, link = true}) {
    const g = SG_PAGES.sources.groups.find(x => x.id === id);
    return el("li", {class: "src-way", "data-way": id, "data-state": state || null},
      el("h3", {class: "src-way-t"}, icon(ico || g.icon),
        link ? el("a", {href: "#" + id, text: title || g.title}) : el("span", {text: title || g.title})),
      status ? el("p", {class: "src-way-st", text: status}) : null,
      last ? el("p", {class: "src-way-last", text: last}) : null,
      more || null);
  }
  function portals(conns, imports) {
    const live = conns.filter(c => c.state !== "disconnected");
    if (!live.length) return {state: "off", status: "Not set up", last: "Sign in to a patient portal or health plan once, and new records come in by themselves."};
    const brought = Math.max(0, ...imports.filter(e => e.origin === "sync" && e.state === "active").map(e => e.at));
    const synced = Math.max(0, ...live.map(c => c.last_sync || 0)), bad = live.filter(c => c.state === "needs reconnect" || c.warning);
    const n = live.length === 1 ? "Connected" : `${live.length} connected`;
    return {state: bad.length ? "bad" : "ok", status: synced ? `${n} · synced ${ago(synced)}` : `${n} · not synced yet`,
      last: brought ? `Last brought records ${when(brought)}` : "Hasn't brought records yet",
      more: el("ul", {class: "src-way-list"}, ...live.map(c => {
        const ended = c.state === "needs reconnect", warn = c.warning && c.warning.text;
        return el("li", {class: ended || warn ? "bad" : null}, ended || warn ? icon("triangle-alert", {label: "Warning:"}) : null,
          el("b", {text: c.source}), ` · ${ended ? "access ended" : warn ? warn : c.phase === "first pull preview" ? "first pull ready to review"
            : c.phase === "first pull" ? "pulling its records for the first time…" : "syncing on a schedule"}`);
      }))};
  }
  function phone(st) {
    const set = st.has_secret || st.legacy;
    return !set ? {state: "off", status: "Not set up", last: "Steps, sleep, heart rate and weight, sent by the phone app."}
      : {state: "ok", status: st.last ? `Connected · last upload ${ago(st.last)}` : "Set up · nothing received yet",
         last: st.last ? `Last upload ${when(st.last)}` : null};
  }
  const by = e => e.by ? ` by ${e.by}` : "";
  function uploads(imports) {
    const e = imports.find(x => x.origin === "upload" && x.state === "active");   // newest first
    return e ? {state: "ok", status: `Last upload ${ago(e.at)}`, last: `${e.name || e.label} · ${when(e.at)}${by(e)}`}
      : {state: "off", status: "Nothing uploaded yet", last: "The export you downloaded from a portal or app: a zip, a folder or single files."};
  }
  function typed(imports) {
    const e = imports.find(x => x.origin === "manual" && x.state === "active");
    return e ? {state: "ok", status: `Last typed in ${ago(e.at)}`, last: `${e.name || "Typed in"} · ${when(e.at)}${by(e)}`}
      : {state: "off", status: "Nothing typed in yet", last: "Records on paper, or from a portal with no export."};
  }

  /* nothing has come in yet: what would be here, a picture of the ways in, and the first step */
  function empty(edit) {
    const e = emptyState(edit ? "Nothing has come into this profile yet: each upload, record typed in, portal and phone that brings records is listed here."
      : "Nothing has come into this profile yet: what brings it records is listed here.", edit ? {href: "#upload", text: "Upload files"} : null, {cls: "src-empty"});
    e.prepend(el("div", {class: "src-pic", "aria-hidden": "true"},
      ...["upload", "plug", "watch"].map(i => el("span", {class: "src-pic-way"}, icon(i))), icon("arrow-right"),
      el("span", {class: "src-pic-to"}, icon("database"))));
    return e;
  }

  async function load() {
    const pid = SgSources.pid(), mine = ++seq, edit = SgSources.canEdit();
    if (!pid) return;
    let conns, imps, watch = null, jobs = null;
    try {
      [conns, imps, watch, jobs] = await Promise.all([get(`profiles/${pid}/connections`), get(`profiles/${pid}/imports`),
        edit ? get(`profiles/${pid}/watch/receiver`).catch(() => null) : null, edit ? get(`profiles/${pid}/jobs?state=preview`).catch(() => null) : null]);
    } catch (e) {
      if (mine === seq) { $("#srcList").replaceChildren(el("p", {class: "msg bad", role: "alert", text: "Couldn't read this profile's sources: " + e.message})); box.hidden = false; }
      return;
    }
    if (mine !== seq) return;   // another profile was chosen meanwhile
    const imports = imps.imports || [], items = edit && jobs ? sgWaiting(jobs, conns) : [];
    drawWaiting(items);
    if (jobs) SgNav.waited(pid, items.length);   // Add's count (nav.js)
    const done = imports.filter(e => e.state === "active"), total = done.length, live = conns.connections.filter(c => c.state !== "disconnected");
    const set = watch && (watch.has_secret || watch.legacy);
    const ways = [
      edit || live.length ? way({id: "portals", link: edit, ...portals(conns.connections, imports)}) : null,
      edit && watch ? way({id: "phone", ...phone(watch)}) : null,
      edit || imports.some(x => x.origin === "upload") ? way({id: "upload", title: edit ? null : "Uploads", link: edit, ...uploads(imports)}) : null,
      edit || imports.some(x => x.origin === "manual") ? way({id: "manual", title: edit ? null : "Typed in", link: edit, ...typed(imports)}) : null,
      edit ? way({id: "link", state: "", last: "A SMART Health Link or its QR code, from a provider, insurer or app."}) : null,
      way({id: "history", state: total ? "ok" : "off", status: total ? plural(total, "import", "imports") : "Nothing imported yet",
           last: total ? `Latest: ${done[0].name || done[0].label} · ${when(done[0].at)}` : null})].filter(Boolean);   // (the setup build's too)
    const nothing = !live.length && !total && !set;
    $("#srcList").replaceChildren(...[nothing ? empty(edit) : null, nothing && !edit ? null : el("ul", {class: "src-ways"}, ...ways)].filter(Boolean));
    box.hidden = false;
  }

  (async () => {
    if (!await SgSources.ready) return;
    $("#profile").addEventListener("change", () => { review.replaceChildren(); all = false; load(); });
    document.addEventListener("sources:group", ev => { if (ev.detail === "overview") load(); });
    // an import of the profile saved or undone, wherever it was (import-jobs.js told, #513: a Review here added, a
    // toast's Undo): the cards and what's waiting read again, if the Overview is shown (sidebar.js's grp-off); else
    // when it's next shown, not refreshing every waiting preview meanwhile beside the one under review (#521)
    document.addEventListener("sg:imports", ev => { if (ev.detail.pid === SgSources.pid() && !box.classList.contains("grp-off")) ev.detail.wait(load()); });
    load();
  })();
})();
