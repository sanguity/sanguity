/* Sanguity · Sources → Overview (#166): every way records reach the profile chosen, and when each last brought
   something, at a glance: its connected portals and health plans (with the warning of one that stopped syncing,
   #102), its phone and watch (for its owners and editors, who set it up), and its latest uploads and records typed
   in. Each row opens its section. Read again when the profile changes and each time the Overview is shown.
     GET api/profiles/{pid}/connections      what each connection is doing, and when it last pulled (connect.js's)
     GET api/profiles/{pid}/imports          every import, a sync's naming its connection (import-history.js's)
     GET api/profiles/{pid}/watch/receiver   the phone's setup and its last upload (owners and editors) */
"use strict";   // $, el and api come from util.js
(() => {
  const RECENT = 5;   // uploads shown
  const ORIGIN = {upload: "Uploaded", manual: "Typed in", dataset: "Loaded from the command line", build: "From the setup build"};
  const when = fmtWhen;   // dates.js
  const box = $("#srcOverview");
  let seq = 0;

  async function get(path) {
    const r = await api(path);
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
    return j;
  }

  /* one way in: what it is, how it's doing, when it last brought something, and a link to its section */
  function row({name, tag, state, last, bad, href, link}) {
    return el("li", {class: "src-row" + (bad ? " bad" : "")},
      el("div", {class: "src-row-t"},
        el("div", {}, el("b", {text: name}), tag ? el("span", {class: "tag", text: tag}) : null),
        state ? el("p", {class: bad ? "msg bad" : "muted", text: state}) : null,
        el("p", {class: "src-last", text: last})),
      el("a", {class: "btn small", href, text: link}));
  }
  const part = (title, rows, empty) => [el("h3", {class: "sub", text: title}),
    rows.length ? el("ul", {class: "src-rows"}, ...rows) : el("p", {class: "muted", text: empty})];

  function connRows(conns, imports) {
    const brought = {};   // a connection's newest sync that brought records, by its name (the history names it so)
    for (const e of imports) if (e.origin === "sync" && e.state === "active" && e.connection) brought[e.connection] = Math.max(brought[e.connection] || 0, e.at);
    return conns.filter(c => c.state !== "disconnected").map(c => {
      const ended = c.state === "needs reconnect", warn = c.warning && c.warning.text;
      const state = ended ? "Access ended: sign in again to keep it up to date." : warn ? warn
        : c.phase === "first pull preview" ? "Its first pull is waiting for you to look at it and save it."
        : c.phase === "first pull" ? "Pulling its records for the first time…" : "Syncing on a schedule.";
      return row({name: c.source, tag: c.host, state, bad: ended || !!warn, href: "#conn-" + c.id, link: "Open",
        last: (brought[c.source] ? `Last brought records ${when(brought[c.source])}` : "Hasn't brought records yet") +
          (c.last_sync ? ` · last checked ${when(c.last_sync)}` : "")});
    });
  }
  function phoneRows(st) {
    if (!st) return [];
    const set = st.has_secret || st.legacy;
    return [row({name: "Phone app", tag: set ? null : "not set up", href: "#phone", link: set ? "Open" : "Set it up",
      last: st.last ? `Last upload ${when(st.last)}` : set ? "Nothing received yet" : "No phone sends readings to this profile yet."})];
  }
  function uploadRows(imports) {
    return imports.filter(e => e.origin !== "sync" && e.state === "active").slice(0, RECENT).map(e =>
      row({name: e.name || e.label, tag: e.origin === "build" ? null : ORIGIN[e.origin] || e.origin, href: "#import-" + e.id, link: "In the history",
        last: `${when(e.at)} · ${e.by}`}));
  }

  async function load() {
    const pid = SgSources.pid(), mine = ++seq;
    if (!pid) return;
    let conns, imps, watch = null;
    try {
      [conns, imps, watch] = await Promise.all([get(`profiles/${pid}/connections`), get(`profiles/${pid}/imports`),
        SgSources.canEdit() ? get(`profiles/${pid}/watch/receiver`).catch(() => null) : null]);
    } catch (e) {
      if (mine === seq) { $("#srcList").replaceChildren(el("p", {class: "msg bad", role: "alert", text: "Couldn't read this profile's sources: " + e.message})); box.hidden = false; }
      return;
    }
    if (mine !== seq) return;   // another profile was chosen meanwhile
    const edit = conns.can_connect, imports = imps.imports || [];
    const warned = conns.connections.filter(c => c.state === "needs reconnect" || (c.warning && c.state !== "disconnected"));
    $("#srcWarn").replaceChildren(...warned.map(c => el("p", {class: "conn-alert src-alert", role: "alert"},
      el("span", {text: "⚠ " + (c.warning ? c.warning.text : `${c.source}: access ended.`) + " "}),
      el("a", {href: "#conn-" + c.id, text: edit ? "Fix it" : "Details"}))));
    const conn = connRows(conns.connections, imports), ups = uploadRows(imports), phone = phoneRows(watch);
    const total = imports.filter(e => e.state === "active").length;
    if (!conn.length && !ups.length && !(watch && (watch.has_secret || watch.legacy))) {   // nothing yet: one empty state (#199)
      $("#srcList").replaceChildren(emptyState("Nothing has come into this profile yet: each upload, record typed in, portal and phone that brings records is listed here.",
        edit ? {href: "#upload", text: "Upload files"} : null, {cls: ""}));
      box.hidden = false;
      return;
    }
    $("#srcList").replaceChildren(...[   // without the parts left out: replaceChildren would write a null out as text
      ...part("Portals and health plans", conn, edit ? "None connected yet." : "None connected."),
      ...(edit ? [el("p", {class: "src-add"}, el("a", {href: "#portals", text: conn.length ? "Connect another…" : "Connect a portal or health plan…"}))] : []),
      ...(phone.length ? part("Phone and watch", phone, "") : []),
      ...part("Uploads and records typed in", ups, "Nothing uploaded or typed in yet."),
      total ? el("p", {class: "src-add"}, el("a", {href: "#history", text: `All ${total} import${total === 1 ? "" : "s"}, with undo, in the import history`})) : null].filter(Boolean));
    box.hidden = false;
  }

  (async () => {
    if (!await SgSources.ready) return;
    $("#profile").addEventListener("change", load);
    document.addEventListener("sources:group", ev => { if (ev.detail === "overview") load(); });
    load();
  })();
})();
