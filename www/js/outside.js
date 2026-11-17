/* Sanguity · Admin page, "Outside connections" (#59, #97; there since #167): every host this install may contact,
   grouped by purpose, with the scopes asked for and granted and when each was last contacted (GET /api/outside,
   outside.py). Admins see the whole install. Read only: connecting and disconnecting are on the Connected sources
   page. admin.js calls SgOutside.load() once it knows the person is an admin. */
"use strict";   // $ and el come from util.js
const SgOutside = (() => {
  const when = t => t ? fmtDT(t * 1000) : "never";   // this person's time zone and format (dates.js)
  const code = t => el("code", {text: t});
  const sub = text => el("h3", {class: "sub", text});
  const dl = rows => el("dl", {}, ...rows.filter(r => r).flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {}, v)]));
  const times = n => n === 1 ? "once" : `${n} times`;
  const profileOf = e => e.profile_name || (e.profile == null ? "no profile (an admin's test)" : "profile " + e.profile);

  function source(s) {
    const h = s.hosts || {};
    const box = el("div", {class: "out-src"},
      el("h4", {text: s.name + (s.vendor ? ` (${s.vendor})` : "")}),
      dl([["FHIR server", code(h.issuer || "")],
          h.token && ["Sign-in tokens", code(h.token)],
          h.revocation && ["Revoking access", code(h.revocation)],
          h.registration && ["Registering this install", code(h.registration)],
          ["Scopes asked for", s.scopes_requested ? code(s.scopes_requested) : "—"],
          ["Last contact", when(s.last_contact)]]));
    if (!s.connections.length)
      box.append(el("p", {class: "muted", text: "Set up, with no profile connected: contacted only when an admin tests it or someone connects a profile."}));
    for (const c of s.connections)
      box.append(el("div", {class: "out-conn"}, dl([
        ["Profile", profileOf(c)],
        ["State", c.state],
        ["Scopes granted", c.scopes_granted ? code(c.scopes_granted) : "—"],
        ["Last contact", when(c.last_contact)],
        ["Last pull", when(c.last_sync)]])));
    return box;
  }

  function contacts(list, empty) {
    if (!list.length) return el("p", {class: "muted", text: empty});
    return el("ul", {class: "out-list"}, ...list.map(e => el("li", {}, code(e.host), ` · ${profileOf(e)} · `,
      e.purpose.replace(/^Connected source: /, "connected source, ") + ` · last ${when(e.last)}, ${times(e.count)}`)));
  }

  async function load() {
    await datesReady();   // times below in this person's zone and format (dates.js; read once per page)
    const msg = $("#outsideMsg");
    let v;
    try {
      const r = await api("outside");
      if (r.status === 401) return;   // admin.js sends the page to sign-in
      if (!r.ok) throw new Error(r.status + " " + r.statusText);
      v = await r.json();
    } catch (e) {
      msg.textContent = "Couldn't load the outside connections: " + e.message;
      msg.classList.add("bad");
      return;
    }
    $("#outsideLead").textContent = v.nothing_else;
    $("#outsideScope").textContent = v.admin ? "You're an admin, so this lists the whole install."
      : "This lists the profiles you own or edit. An admin sees the whole install.";
    const body = $("#outsideBody");
    body.replaceChildren(
      sub("May contact now"),
      v.may_contact.length ? el("ul", {class: "out-hosts"}, ...v.may_contact.map(h => el("li", {}, code(h))))
        : el("p", {class: "muted", text: "No host: no source is set up or connected, so Sanguity contacts nobody on its own."}),
      el("p", {class: "muted"}, "A Health Link's host is contacted only when someone pastes the link and confirms it. ",
        el("a", {href: "connect.html", text: "Connect or disconnect sources"}), "."),
      sub("Connected sources"),
      ...(v.sources.length ? v.sources.map(source) : [el("p", {class: "muted", text: "None."})]),
      sub("SMART Health Link fetches"),
      contacts(v.health_links, "None yet."),
      sub("Health Card issuers' keys"),
      contacts(v.card_keys, "None yet."),
      ...(v.earlier.length ? [sub("Earlier contacts, no longer allowed"), contacts(v.earlier, "")] : []));
  }
  return {load};
})();
