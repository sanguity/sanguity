/* Sanguity · find your provider (#93), in the connect step of Sources → Portals and health plans (#166): search the
   directory on this server (GET api/directory), pick an entry, and see whether it can be connected here. When a
   source this server has set up reaches the same FHIR base, it's connected from there (window.SgConnect, connect.js);
   otherwise an admin is sent to set it up in Settings' Install, with the address filled in, where each vendor's steps
   are (one copy of them: Settings → Connected-source apps), and everyone else is told what to ask their admin for.
   Logos are shown only when the directory file carries them as data: URLs; nothing is fetched from vendors. */
"use strict";   // $, el and api come from util.js
(() => {
  let CTX = null, seq = 0, timer = 0, current = null, ADMIN = false, shown = null;

  async function call(path) {
    const r = await api(path);
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
    return j;
  }
  const say = (text, bad = false) => sayIn($("#status"), text, bad);   // shown and said (util.js, #209)
  const kindLabel = {provider: "Provider", payer: "Insurer", sandbox: "Test sandbox"};

  /* a value in a field, with the app's one Copy (util.js copyBtn, #495: it selects it instead where the clipboard isn't
     available, plain HTTP) */
  function copyBox(value, label) {
    const input = el("input", {value, readonly: "", "aria-label": label, class: "dir-copy-v", spellcheck: "false"});
    input.addEventListener("focus", () => input.select());
    return el("div", {class: "dir-copy"}, input, copyBtn(value, {what: "the " + label, sel: input}));
  }

  /* ---------------------------------------------------------------- which lists this server has */
  /* what's missing, and who can add it: an admin, in Settings → Install → Provider directory (admin-sources.js, #992) */
  function sources(list) {
    const missing = list.filter(s => !s.included);
    const box = $("#srcNote");
    box.hidden = !missing.length;
    if (!missing.length) return;
    const names = missing.map(s => s.label.split(":")[0].replace(/ Millennium.*/, "")).join(" and ");
    box.replaceChildren(
      el("p", {}, el("b", {text: "This server has only the vendors' test sandboxes and Medicare so far. "}),
        `${names}'s hospitals and clinics aren't in its list yet: their lists can't be shipped with Sanguity, so each server adds its own copy. `,
        ...(ADMIN ? ["As an admin, you can have this server read them: ", el("a", {href: "settings.html#directory", text: "Add them now"}), "."]
          : ["Ask your admin to add them."])));
  }

  /* ---------------------------------------------------------------- searching */
  function results(list, q) {
    const ul = $("#results");
    ul.replaceChildren(...list.map(r => el("li", {},
      el("button", {type: "button", class: "dir-hit" + (current === r.id ? " sel" : ""), "data-id": r.id, onclick: () => open(r.id, true)},
        el("span", {class: "dir-mono", "aria-hidden": "true", text: (r.name.match(/[A-Za-z0-9]/) || ["?"])[0].toUpperCase()}),
        el("span", {class: "dir-hit-t"},
          el("b", {text: r.name}),
          el("span", {class: "muted", text: [CTX.vendors[r.vendor].name, r.kind !== "provider" ? kindLabel[r.kind] : null,
            r.place && (r.places > 1 ? `${r.place} and ${r.places - 1} more` : r.place)].filter(Boolean).join(" · ")}),
          r.matched ? el("span", {class: "muted", text: "includes " + r.matched}) : r.org && r.org !== r.name ? el("span", {class: "muted", text: "part of " + r.org}) : null)))));
    say(!q ? "" : list.length ? `${list.length === 25 ? "The first 25" : list.length} match${list.length === 1 ? "" : "es"}.`
      : `Nothing matches “${q}”. Try fewer words, or the city.`);
  }

  async function search(q) {
    const mine = ++seq;
    try {
      const j = await call("directory?q=" + encodeURIComponent(q));
      if (mine !== seq) return;   // a newer search has started
      CTX = j;
      sources(j.sources);
      results(q.trim() ? j.results : [], q.trim());
    } catch (e) { if (mine === seq) say(e.message, true); }
  }
  $("#q").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const q = $("#q").value;
      const u = new URL(location.href); q.trim() ? u.searchParams.set("q", q) : u.searchParams.delete("q");
      history.replaceState(null, "", u);
      search(q);
    }, 200);
  });
  $("#q").addEventListener("keydown", ev => {
    if (ev.key === "Enter") { const b = $("#results .dir-hit"); if (b) { ev.preventDefault(); b.click(); } }
    if (ev.key === "ArrowDown") { const b = $("#results .dir-hit"); if (b) { ev.preventDefault(); b.focus(); } }
  });
  $("#results").addEventListener("keydown", ev => {   // up and down move through the results
    const li = ev.target.closest("li");
    if (!li || !["ArrowDown", "ArrowUp"].includes(ev.key)) return;
    ev.preventDefault();
    const to = ev.key === "ArrowDown" ? li.nextElementSibling : li.previousElementSibling;
    to ? to.querySelector("button").focus() : ev.key === "ArrowUp" && $("#q").focus();
  });

  /* ---------------------------------------------------------------- one provider, and what connecting it needs */
  async function open(id, scroll) {
    let j;
    try { j = await call("directory/entry?id=" + encodeURIComponent(id)); }
    catch (e) { say(e.message, true); return; }
    current = id;
    for (const b of document.querySelectorAll(".dir-hit")) b.classList.toggle("sel", b.dataset.id === id);
    const u = new URL(location.href); u.searchParams.set("id", id); history.replaceState(null, "", u);
    show(j);
    if (!scroll) return;
    const box = $("#detail"), h = box.querySelector("h3");
    box.scrollIntoView({behavior: smoothly(), block: "start"});
    // the details under the list, not beside it: focus to their heading, as the result chosen is scrolled off the screen
    // and the next Tab went back to the list (#1067)
    if (box.getBoundingClientRect().top >= $("#results").getBoundingClientRect().bottom - 1) { h.tabIndex = -1; h.focus({preventScroll: true}); }
  }

  /* the sources set up on this server that reach this entry's FHIR base (connect.js lists them) */
  const base = u => String(u || "").trim().toLowerCase().replace(/\/+$/, "");
  const setUp = e => (window.SgConnect ? SgConnect.sources : []).filter(s => base(s.issuer) === base(e.fhir_base));
  /* Settings → Connected-source apps' vendor for a directory vendor, to open its setup with this entry filled in */
  const ADMIN_VENDOR = {epic: "epic", oracle: "oracle", "cms-bluebutton": "insurer"};
  function connecting(e, v) {
    const all = setUp(e), here = all.filter(s => !s.waiting);
    if (all.length && !here.length) return [   // waiting for the vendor's review (#165): upload the portal's export meanwhile
      ...all.map(s => el("p", {}, el("b", {text: "Not ready yet. "}), s.note)),
      SgConnect.canConnect ? el("div", {class: "row"}, el("a", {class: "btn small", href: "#upload", text: "Upload files"})) : null];
    if (here.length && SgConnect.canConnect) return [
      el("p", {}, el("b", {text: "Set up on this server. "}), "Sign in to it to connect this profile."),
      el("div", {class: "row"}, ...here.map(s => el("button", {class: "btn primary", type: "button", text: `Connect ${s.name}…`,
        onclick: () => SgConnect.connect(s.id)})))];
    if (here.length) return [el("p", {}, el("b", {text: "Set up on this server. "}), "This profile's owners and editors can connect it.")];
    if (ADMIN) {
      const q = new URLSearchParams({vendor: ADMIN_VENDOR[e.vendor] || "other", issuer: e.fhir_base, name: e.name});
      return [el("p", {}, el("b", {text: "Not set up on this server yet. "}), "As an admin, you set it up under ",
        el("a", {href: `settings.html?${q}#apps`, text: "Settings → Connected-source apps"}),
        `, which has ${v.name}'s steps, with this FHIR base filled in. Then each profile connects it by signing in.`),
        copyBox(e.fhir_base, "FHIR base URL")];
    }
    return [el("p", {}, el("b", {text: "Not set up on this server yet. "}),
      `Ask your admin to set up ${v.name} for ${e.name}: they'll need its FHIR base, below. Then you connect it here by signing in.`),
      copyBox(e.fhir_base, "FHIR base URL")];
  }
  document.addEventListener("connect:sources", () => { if (shown) show(shown); });   // another profile, or read again

  function show(j) {
    shown = j;
    const {entry: e, vendors} = j, v = vendors[e.vendor];
    const logo = typeof e.logo === "string" && /^data:image\/(png|jpeg|gif|webp);base64,/.test(e.logo)
      ? el("img", {class: "dir-logo", src: e.logo, alt: ""})
      : el("span", {class: "dir-mono big", "aria-hidden": "true", text: (e.name.match(/[A-Za-z0-9]/) || ["?"])[0].toUpperCase()});
    const places = (e.places || []).filter(p => p[0]).concat((e.places || []).filter(p => !p[0]))   // with a city first
      .map(p => p.filter(Boolean).join(", ")).filter(Boolean);
    const more = (list, total) => total > list.length ? ` and ${total - list.length} more` : "";
    const caution = v.realistic !== "possible";
    const box = $("#detail");
    box.replaceChildren(...[   // replaceChildren would write a null out as text
      el("div", {class: "dir-hd"}, logo, el("div", {},
        el("h3", {text: e.name}),
        el("div", {}, el("span", {class: "tag", text: v.name}), el("span", {class: "tag", text: kindLabel[e.kind] || e.kind}),
          e.fhir_version ? el("span", {class: "tag", text: "FHIR " + e.fhir_version}) : null),
        e.org && e.org !== e.name ? el("p", {class: "muted", text: "Part of " + e.org}) : null)),
      places.length ? el("p", {class: "muted dir-places", text: places.slice(0, 12).join(" · ") + more(places.slice(0, 12), e.places_total || places.length)}) : null,

      el("h4", {class: "sub", text: "Connecting it"}),
      ...connecting(e, v),
      el("p", {class: "dir-real" + (caution ? " caution" : "")}, el("b", {text: v.realistic_label + ". "}), v.note),

      e.aliases && e.aliases.length ? el("details", {class: "dir-aka"},
        el("summary", {text: `Its ${e.aliases_total || e.aliases.length} location${(e.aliases_total || e.aliases.length) === 1 ? "" : "s"} and other names`}),
        el("p", {class: "muted", text: e.aliases.join(" · ") + more(e.aliases, e.aliases_total || e.aliases.length)})) : null,
      el("p", {class: "muted dir-from"}, `From ${e.source === "curated" ? "the vendor's documentation" : e.source === "epic-brands" ? "Epic's published list" : "Oracle Health's published list"}${e.retrieved ? `, read ${fmtD(e.retrieved)}` : ""}.`),
    ].filter(Boolean));
    box.hidden = false;
  }

  /* ---------------------------------------------------------------- start */
  (async () => {
    const me = await SgSources.ready;   // the page signs in (sources-page.js)
    if (!sgConnected(me)) return;   // (#1096) off, with connected sources
    ADMIN = me.user.is_admin;
    $("#finder").hidden = false;
    const p = new URLSearchParams(location.search);   // providers.html?q=…&id=… (moved.js keeps them)
    $("#q").value = p.get("q") || "";
    await search($("#q").value);
    if (p.get("id")) open(p.get("id"), false);
  })();
})();
