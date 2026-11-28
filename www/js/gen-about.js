/* Sanguity · Settings → Install → About (#1004, #998; General's before #1153): this server's version (a checkout says it has none),
   its database's schema version, how it's deployed (SANGUITY_DEPLOYMENT, each template's), who it runs as, its
   folders and configuration files, each with what mounts or sets it on this kind of install, a link to the releases
   page (only a link: nothing is contacted, #998's decision 5), what only the server's own setup sets (#1119), and the
   limits with their variables (changed under Needs a restart, #1121), one the environment sets tagged. All of it from GET settings/install/about (server/general.py). call() and plural() are
   settings.js's; gbLines is gen-backups.js's. */
"use strict";
async function loadGenAbout() {
  let a;
  try { a = await call("settings/install/about"); }
  catch (e) { $("#genAbout").replaceChildren(el("p", {class: "msg bad", text: e.message})); return; }
  const n = v => typeof v === "number" ? v.toLocaleString("en-US") : String(v);
  const s = a.server, env = (what, name, v) => v.from === "environment" ? [what, ...envTag(name)] : what;
  const secure = {"1": "Always", "0": "Never"}[String(s.cookie_secure.value).trim()] ||
    `Automatic: Secure when the page is opened over HTTPS${location.protocol === "https:" ? ", as this one is" : ""}`;
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
    // (#1119: Host's and Security's before) what only the server's own setup sets; the trusted proxies and the password
    // checks at once are Needs a restart's (#1121)
    el("h3", {class: "sub", text: "Server"}),
    gbLines([["Internal port and bind address", [el("code", {text: `${s.bind}:${s.port}`}), ": where the app listens inside its container, for nginx"],
        "PORT and HOST in the app's own environment, which no compose file changes"],
      [env("Secure session cookie", "COOKIE_SECURE", s.cookie_secure), secure + ".", "COOKIE_SECURE in .env (the Secure cookie field on TrueNAS or Unraid)"]]),
    el("h3", {class: "sub", text: "Limits"}),
    el("p", {class: "hint"}, "Changed under ", el("a", {href: "#gen-restart", text: "General, Needs a restart"}), ", where a variable set in .env wins; the zip ratio only in .env."),   // (#1153: another group's)
    ...a.limits.flatMap(g => [el("p", {}, el("b", {text: g.group})),
      el("ul", {}, ...g.limits.map(l => el("li", {}, `${l.what}: `, el("b", {text: `${n(l.value)} ${l.unit}`}),
        ...(l.from === "environment" ? envTag(l.name) : [" (", el("code", {text: l.name}), l.restart ? ")" : ", in .env only)"]))))]));
}
