/* Sanguity · Settings → Install → General: Host (#1000). The public addresses (PUBLIC_ORIGINS), editable but guarded:
   the bar's Save asks the server what the change does first (routes_host.py: the address given out, the connected
   sources' return and the phone upload address, with the counts of what must be changed elsewhere), then opens a
   confirmation to type, then the fresh check; a list this page's own address wouldn't pass is refused before. Beside
   it, read-only: the passkeys' names, what this page's request carried, and what only the server's setup sets, each
   with where. admin.js starts it (adminStart: loadHost); settings-nav.js's bar saves it (ADM: hostSave, hostDiscard). */
"use strict";

let HOST = null, HOST_ZONE = null, HOST_REAUTH = null;
const hostReauth = () => HOST_REAUTH || (HOST_REAUTH = el("div", {id: "hostReauth", class: "reauth", hidden: true}));
/* the text typed, as the list it would be saved as (the server cleans it again) */
const hostList = () => $("#hostOrigins").value.split(/[\s,]+/).filter(Boolean);
const hostDirty = () => !!HOST && HOST.st.can_change && !same(hostList(), HOST.st.value);

async function loadHost(note) {
  const card = $("#gen-host");
  let d, h;
  try { [d, h] = await Promise.all([call("settings/install"), call("settings/host?origin=" + encodeURIComponent(location.origin))]); }
  catch (e) { card.replaceChildren(el("h2", {text: "Host"}), el("p", {class: "msg bad", text: e.message})); return; }
  const sec = d.sections.find(s => s.id === "host");
  if (!sec) { card.hidden = true; return; }
  drawHost(sec, h, note);
}

function drawHost(sec, h, note) {
  const card = $("#gen-host"), st = sec.settings.find(s => s.key === "host.publicOrigins"), locked = !st.can_change;
  HOST = {sec, st, h};
  const msg = el("span", {class: "msg set-msg"});
  const box = el("textarea", {id: "hostOrigins", rows: Math.max(2, st.value.length + 1), spellcheck: "false", autocapitalize: "none",
    autocomplete: "off", "aria-labelledby": "hostOriginsName", "aria-describedby": "hostOriginsHint", readonly: locked || null});
  box.value = st.value.join("\n");
  const row = el("div", {class: "set set-row", "data-key": st.key});
  const mark = () => { const d = hostDirty(); row.classList.toggle("set-changed", d); d ? card.setAttribute("data-dirty", 1) : card.removeAttribute("data-dirty"); };
  box.oninput = () => { HOST_ZONE?.node.setAttribute("hidden", ""); mark(); };   // (what was said is for the list before)
  HOST_ZONE = dangerZone({title: "Change the public addresses", action: "Change addresses", says: () => HOST_ZONE.lines, run: hostChange});
  HOST_ZONE.node.hidden = true;
  row.append(
    el("div", {class: "set-row-lbl"}, el("h3", {class: "sub", id: "hostOriginsName"}, st.label,
      st.from === "environment" ? el("span", {class: "tag", text: "set by the environment"}) : null,
      el("span", {class: "tag set-unsaved", text: "not saved"})),
      el("div", {id: "hostOriginsHint"}, helpNodes(st.help))),
    el("div", {class: "set-row-ctl"}, box,
      locked ? el("p", {class: "hint", text: `Set by ${st.env} in the server's environment (.env, or the app's settings on TrueNAS or ` +
        "Unraid), so it's changed there."}) : null));
  const req = h.request, by = {listed: "it's one of the public addresses", host: "it's the host the request reached"}[req.accepted_by];
  const ro = (name, where, ...value) => setRow([el("h3", {class: "sub", text: name}), el("p", {class: "hint", text: where})], value);
  const port = (req.forwarded_host || req.host || "").match(/:(\d+)$/)?.[1];
  card.replaceChildren(...present(
    el("div", {class: "set-hd"}, el("h2", {id: "gen-host-hd", tabindex: -1, text: sec.title})),
    helpNodes(sec.help), msg, hostReauth(), row, HOST_ZONE.node,
    ro("Passkey names", "A passkey works only at the name it was made at: at another address, people sign in another way and can make one there.",
      h.passkeys.length ? h.passkeys.map(p => el("p", {}, el("code", {text: p.name}), `: ${plural(p.count, "passkey")}`)) : "None made yet."),
    ro("This page's request", "What reached the server for this page, after any reverse proxy in front of it.",
      el("dl", {class: "kv"},
        el("dt", {text: "Host"}), el("dd", {}, el("code", {text: req.host || "none"})),
        el("dt", {text: "Forwarded host"}), el("dd", {}, req.forwarded_host ? el("code", {text: req.forwarded_host}) : "none"),
        el("dt", {text: "Scheme"}), el("dd", {text: req.forwarded_proto ? `${req.scheme} (forwarded: ${req.forwarded_proto})` : req.scheme}),
        el("dt", {text: "Let in"}), el("dd", {text: by ? `${location.origin}: ${by}` : `${location.origin}: not by either rule`}))),
    ro("Trusted proxies", "Whose forwarded headers the server believes. Set on the app's own line in compose.yaml (and in the TrueNAS and CasaOS " +
      "templates): only nginx reaches the app there.", el("code", {text: h.proxies})),
    ro("Internal port and bind address", "Where the app listens inside its container, for nginx. Set by PORT and HOST in the app's own " +
      "environment, which no compose file changes.", el("code", {text: `${h.bind}:${h.port}`})),
    ro("Published web port", "Where this machine publishes the web app: WEB_PORT in .env (8730 unless set; compose.yaml's web ports line), " +
      "WebUI Port on TrueNAS, Web UI port on Unraid.", port ? `This page came in on port ${port}.` : "Not seen by the app here.")));
  if (note) sayIn(msg, note, false, {say: false});
  mark();
}

/* the bar's Save: what the change does, from the server, then the confirmation to type (its red button saves) */
async function hostSave(msg) {
  if (!hostDirty()) return;
  let e;
  try { e = await call(`settings/install/${HOST.st.key}/effects`, "POST", {value: hostList()}); }
  catch (err) { sayIn(msg, `The public addresses aren't saved: ${err.message}`, true); return; }
  if (!e.accepted) {
    sayIn(msg, `The public addresses aren't saved: this page is open at ${e.origin}, and with that list the cross-site check would refuse ` +
      `everything it sends, changing it back included. Add ${e.origin} to it.`, true);
    return;
  }
  const given = a => a ? el("code", {text: a}) : "none (each page gives the address it was opened at)";
  HOST_ZONE.value = e.value;
  HOST_ZONE.lines = present(
    el("p", {}, "The address the app gives out: ", given(e.given_out[0]), " becomes ", given(e.given_out[1]), "."),
    e.apps ? el("p", {}, `${plural(e.apps, "connected-source app")} must have the new return address, `, el("code", {text: e.return_address[1] || "none"}),
      ", registered with its vendor before anyone can connect or sign in again there.") : null,
    e.phones ? el("p", {}, `The phones of ${plural(e.phones, "profile")} with an upload secret need the new upload address, `,
      el("code", {text: e.upload[1].join(", ")}), ", in their phone app.") : null,
    !e.apps && !e.phones ? "No connected-source app or phone needs changing." : null);
  HOST_ZONE.node.hidden = false;
  HOST_ZONE.show(e.confirm);
  sayIn(msg, "Not saved yet: confirm the change under Public addresses.");
}
async function hostChange(typed) {
  const was = HOST.st.value, value = HOST_ZONE.value, k = HOST.st.key;
  await fresh(() => call(`settings/install/${k}`, "PUT", {value, was, confirm: typed}), hostReauth());
  HOST_ZONE.hide();
  await loadHost("Saved the public addresses.");
  $("#gen-host-hd").focus();
  toast("Saved the public addresses.");
}
function hostDiscard() {
  if (!HOST) return;
  $("#hostOrigins").value = HOST.st.value.join("\n");
  HOST_ZONE.hide(); HOST_ZONE.node.hidden = true;
  $("#gen-host .set-row").classList.remove("set-changed");
  $("#gen-host").removeAttribute("data-dirty");
  sayIn($("#gen-host .set-msg"), "Changes discarded: back as saved.");
  $("#gen-host-hd").focus();
}
