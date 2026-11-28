/* Sanguity · Settings → Install → General → This server (#1159): the server's name (in the browser's title and the top
   bar, to tell installs apart), checking for new versions (off by default; GitHub's releases only, listed under
   Outside connections; "A new version is available" here, for admins), and the defaults new people start with (their
   time zone, date format and units, which each can still change). Each saved as it's changed, after the fresh check
   (admin.js fresh, in the card). call(), sayIn() and toast() are the page's. */
"use strict";
let GI = null;   // GET settings/install/instance: {name, updates, defaults, set, date_formats, units}
const giMsg = (text, bad) => sayIn($("#genInstMsg"), text, bad);
const giPut = (path, value) => fresh(() => call(path, "PUT", {value}), $("#genInstReauth"));

async function loadGenInstance(note) {
  try { GI = await call("settings/install/instance"); }
  catch (e) { giMsg(e.message, true); return; }
  giDraw(note);
}
function giDraw(note) {
  const back = $("#genInst").contains(document.activeElement) ? document.activeElement.id : null;
  const u = GI.updates, sel = (id, opts, now, label, onchange) => el("select", {id, "aria-label": label, onchange},
    ...opts.map(([v, l]) => el("option", {value: v, text: l, selected: v === now || null})));
  const name = el("input", {id: "giName", maxlength: 40, value: GI.name, autocomplete: "off", "aria-describedby": "giNameH"});
  const upd = el("input", {type: "checkbox", role: "switch", id: "giUpd", checked: u.on ? "" : null, "aria-labelledby": "giUpdLbl", "aria-describedby": "giUpdH giUpdSaid",
    onchange: () => giSwitch(upd)});
  const said = !u.on ? "" : u.newer ? null : u.problem ? `The last check didn't get through: ${u.problem}`
    : u.latest ? `Checked ${fmtDT(u.checked * 1000)}: ${u.current ? `this server runs ${u.current}, and ` : ""}${u.latest} is the latest release.` : "Not checked yet: it's checked once a day.";
  const zones = (Intl.supportedValuesOf?.("timeZone") || []).map(z => [z, z.replace(/_/g, " ")]);
  const d = GI.defaults, unit = d.units || {};
  $("#genInst").replaceChildren(
    setRow([el("label", {for: "giName", text: "Name of this server"}),
      el("p", {class: "hint", id: "giNameH", text: "Shown in the browser's title and the top bar, to tell installs apart (Home, Test). Empty: none."})],
      el("div", {class: "form inline"}, name, el("button", {type: "button", class: "btn", text: "Save name", onclick: () => giName(name.value)}))),
    setRow([el("p", {class: "set-row-name", id: "giUpdLbl", text: "Check for new versions"}),
      el("p", {class: "hint", id: "giUpdH", text: `Off: nobody is asked. On: once a day this server asks GitHub's releases (${u.host}, listed under Outside connections) for Sanguity's latest release; nothing about this server or anyone is sent.`}),
      u.newer ? el("p", {class: "warn", id: "giUpdSaid"}, el("b", {text: "A new version is available: "}), `Sanguity ${u.latest}. `,
        el("a", {href: u.url, rel: "noopener", target: "_blank", text: "Its release notes"}), ".")
        : el("p", {class: "hint", id: "giUpdSaid", text: said})],
      [el("label", {class: "check switch"}, upd), u.on ? el("button", {type: "button", class: "btn", id: "giUpdNow", text: "Check now", onclick: giCheck}) : null]),
    el("h3", {class: "sub", text: "Defaults for new people"}),
    el("p", {class: "hint", text: "What someone starts with when they first sign in. Each person can still change their own, under Settings → Dates and times and Units; nobody who has chosen already is changed."}),
    setRow([el("label", {for: "giZone", text: "Time zone"})],
      sel("giZone", [["", "Their browser's own"], ...zones], d["dates.timeZone"], null, ev => giDefault("dates.timeZone", ev.target.value || null, ev.target))),
    setRow([el("label", {for: "giFormat", text: "Date format"})],
      sel("giFormat", [["", "Their browser's language's"], ...Object.entries(GI.date_formats)], d["dates.format"], null, ev => giDefault("dates.format", ev.target.value || null, ev.target))),
    setRow([el("p", {class: "set-row-name", id: "giUnitsLbl", text: "Units"})],
      el("div", {class: "form inline", role: "group", "aria-labelledby": "giUnitsLbl"}, ...Object.entries(GI.units).map(([part, choices]) =>
        el("label", {}, ({lab: "Lab tests", weight: "Weights", temperature: "Temperatures", height: "Heights"}[part] || part) + " ",
          sel("giUnit-" + part, Object.entries(choices), unit[part] || "reported", null,
            ev => giDefault("units", {...unit, [part]: ev.target.value}, ev.target)))))));
  if (note) giMsg(note);
  if (back) document.getElementById(back)?.focus({preventScroll: true});
}
async function giName(value) {
  try {
    await giPut("settings/install/instance.name", value);
    await loadGenInstance(value.trim() ? `This server is called “${value.trim()}”.` : "This server has no name now.");
    sgInstance(GI.name);   // the title and the top bar (util.js)
    toast($("#genInstMsg").textContent);
  } catch (e) { giMsg(`Not saved: ${e.message}`, true); }
}
async function giSwitch(sw) {
  const on = sw.checked;
  try {
    await giPut("settings/install/updates.check", on);
    await loadGenInstance(on ? "Checking for new versions is on: once a day." : "Checking for new versions is off: nobody is asked.");
    SgOutside.load();   // its host listed, or not
  } catch (e) { sw.checked = !on; giMsg(`Not changed: ${e.message}`, true); }
}
async function giCheck() {
  try { GI = await call("settings/install/updates/check", "POST"); giDraw(GI.updates.problem ? "" : "Checked."); }
  catch (e) { giMsg(e.message, true); }
}
async function giDefault(key, value, ctl) {
  try {
    GI = await giPut(`settings/install/defaults/${encodeURIComponent(key)}`, value);
    giDraw("Saved: new people start with it.");
  } catch (e) { giDraw(); giMsg(`Not saved: ${e.message}`, true); }
  document.getElementById(ctl.id)?.focus({preventScroll: true});
}
