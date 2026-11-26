/* Sanguity · Settings → Install → General → About (#1004, #998): this server's version (a checkout says it has none),
   its database's schema version, how it's deployed (SANGUITY_DEPLOYMENT, each template's), who it runs as, its
   folders and configuration files, each with what mounts or sets it on this kind of install, a link to the releases
   page (only a link: nothing is contacted, #998's decision 5), and the limits with their variables, read-only for now
   (#998's default). All of it from GET settings/install/about (server/general.py). call() and plural() are
   settings.js's; gbLines is gen-backups.js's. */
"use strict";
async function loadGenAbout() {
  let a;
  try { a = await call("settings/install/about"); }
  catch (e) { $("#genAbout").replaceChildren(el("p", {class: "msg bad", text: e.message})); return; }
  const n = v => typeof v === "number" ? v.toLocaleString("en-US") : String(v);
  const from = l => l.from === "environment" ? "set by the environment" : l.value === l.default ? "the default" : "saved";
  $("#genAbout").replaceChildren(
    gbLines([["Version", [a.version ? el("b", {text: a.version}) : null, a.checkout ? (a.version ? " (a checkout of the source)" : "A checkout of the source: no release number") : null,
        ". ", el("a", {href: a.releases, target: "_blank", rel: "noopener noreferrer", text: "Releases and what's new"}), " (on GitHub)"]],
      ["Database", `Schema version ${a.schema}`],
      ["Deployed as", a.deployment_name, null],
      ["Settings", a.settings_at],
      ["Runs as", `User ${a.runs_as.uid}, group ${a.runs_as.gid}`, a.runs_as.set_by]]),
    el("h3", {class: "sub", text: "Folders"}),
    gbLines([...a.folders.map(f => [f.what, el("code", {text: f.path}), f.set_by]),
      ["Configuration", [a.config.folder ? el("code", {text: a.config.folder}) : null, `${a.config.folder ? ": " : ""}${a.config.files.join(", ")}, read as the app starts`], a.config.set_by]]),
    el("h3", {class: "sub", text: "Limits"}),
    el("p", {class: "hint", text: "Read-only here: each is set by its variable, where Settings above says."}),
    ...a.limits.flatMap(g => [el("p", {}, el("b", {text: g.group})),
      el("ul", {}, ...g.limits.map(l => el("li", {}, `${l.what}: `, el("b", {text: `${n(l.value)} ${l.unit}`}), " (", el("code", {text: l.name}), `, ${from(l)})`)))]));
}
