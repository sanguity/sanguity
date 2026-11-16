/* Sanguity · find your provider (#93): search the directory on this server (GET api/directory), pick an entry, and see
   its FHIR base URL and what connecting it needs. Connecting follows option 1 (docs/connected-sources-oauth.md):
   you register your own app with the vendor, so the steps say where and what to paste. Sign-in itself is #32.
   Logos are shown only when the directory file carries them as data: URLs; nothing is fetched from vendors. */
"use strict";   // $, el and api come from util.js
let CTX = null, seq = 0, timer = 0, current = null;

async function call(path) {
  const r = await api(path);
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
  return j;
}
const say = (text, bad = false) => { const m = $("#status"); m.textContent = text; m.classList.toggle("bad", bad); };
const kindLabel = {provider: "Provider", payer: "Insurer", sandbox: "Test sandbox"};
const link = (href, text) => el("a", {href, text: text || href, target: "_blank", rel: "noopener noreferrer"});

$("#logout").onclick = async () => {
  try { await api("logout", {method: "POST"}); } catch (e) {}
  location.replace("login.html");
};

/* copy a value, saying so; falls back to selecting it where the clipboard isn't available (plain HTTP) */
function copyBox(value, label) {
  const input = el("input", {value, readonly: "", "aria-label": label, class: "dir-copy-v", spellcheck: "false"});
  const done = el("span", {class: "msg", role: "status"});
  const btn = el("button", {class: "btn small", type: "button", text: "Copy", onclick: async () => {
    try { await navigator.clipboard.writeText(value); done.textContent = "Copied."; }
    catch (e) { input.focus(); input.select(); done.textContent = "Selected: press Ctrl+C or ⌘C."; }
    setTimeout(() => { done.textContent = ""; }, 2500);
  }});
  input.addEventListener("focus", () => input.select());
  return el("div", {class: "dir-copy"}, input, btn, done);
}

/* ---------------------------------------------------------------- which lists this server has */
function sources(list) {
  const missing = list.filter(s => !s.included);
  const box = $("#srcNote");
  box.hidden = !missing.length;
  if (!missing.length) return;
  box.replaceChildren(
    el("p", {}, el("b", {text: "This server has only the vendors' test sandboxes and Medicare so far. "}),
      `${missing.map(s => s.label.split(":")[0].replace(/ Millennium.*/, "")).join(" and ")}'s published lists can't be shipped with Sanguity under their terms, so an admin adds them to this server once, from the Sanguity folder:`),
    el("pre", {}, el("code", {text: "python3 tools/build_directory.py"})),
    el("ul", {}, ...missing.map(s => el("li", {}, s.label + ": ", link(s.terms, "terms"), ". ", s.why.replace(/ Run tools.*$/, "")))));
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
  if (scroll) $("#detail").scrollIntoView({behavior: "smooth", block: "start"});
}

function show({entry: e, vendors, return_addresses: back}) {
  const v = vendors[e.vendor];
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
      el("h2", {text: e.name}),
      el("div", {}, el("span", {class: "tag", text: v.name}), el("span", {class: "tag", text: kindLabel[e.kind] || e.kind}),
        e.fhir_version ? el("span", {class: "tag", text: "FHIR " + e.fhir_version}) : null),
      e.org && e.org !== e.name ? el("p", {class: "muted", text: "Part of " + e.org}) : null)),
    places.length ? el("p", {class: "muted dir-places", text: places.slice(0, 12).join(" · ") + more(places.slice(0, 12), e.places_total || places.length)}) : null,

    el("h3", {class: "sub", text: "FHIR base URL"}),
    copyBox(e.fhir_base, "FHIR base URL"),

    el("h3", {class: "sub", text: "What connecting needs"}),
    el("p", {class: "dir-real" + (caution ? " caution" : "")}, el("b", {text: v.realistic_label + ". "}), v.note),
    el("p", {}, `Sanguity has no shared app with ${v.name}: each install registers its own, once per vendor. `,
      link(v.developer_site, "Developer site"), " · ", link(v.docs, "documentation")),
    el("ol", {class: "dir-steps"}, ...v.steps.map(s => el("li", {text: s}))),
    el("h3", {class: "sub", text: "Return address to paste"}),
    ...back.map(b => el("div", {class: "dir-back"},
      copyBox(b.url, "Return address"),
      el("p", {class: "muted"}, b.current ? "The address you opened this page at. " : "From PUBLIC_ORIGINS. ",
        b.likely_accepted ? "" : el("span", {class: "bad", text: b.why})))),
    el("h3", {class: "sub", text: "Scopes"}),
    copyBox(v.scopes.join(" "), "Scopes"),
    el("p", {class: "muted", text: v.client}),

    el("div", {class: "row"},
      el("button", {class: "btn primary", type: "button", disabled: "", text: "Connect"}),
      el("span", {class: "muted", text: "Sign-in comes with #32."})),

    e.aliases && e.aliases.length ? el("details", {class: "dir-aka"},
      el("summary", {text: `Its ${e.aliases_total || e.aliases.length} location${(e.aliases_total || e.aliases.length) === 1 ? "" : "s"} and other names`}),
      el("p", {class: "muted", text: e.aliases.join(" · ") + more(e.aliases, e.aliases_total || e.aliases.length)})) : null,
    el("p", {class: "muted dir-from"}, `From ${e.source === "curated" ? "the vendor's documentation" : e.source === "epic-brands" ? "Epic's published list" : "Oracle Health's published list"}, read ${e.retrieved}.`),
  ].filter(Boolean));
  box.hidden = false;
}

/* ---------------------------------------------------------------- start */
(async () => {
  try {
    const me = await call("me");
    $("#who").textContent = me.user.username;
  } catch (e) { return; }
  { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
  $("#finder").hidden = false;
  const p = new URLSearchParams(location.search);
  $("#q").value = p.get("q") || "";
  await search($("#q").value);
  if (p.get("id")) open(p.get("id"), false);
  else $("#q").focus();
})();
