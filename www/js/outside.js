/* Sanguity · Settings → Install, "Outside connections" (#59, #97; the Admin page's from #167, Settings' since #991):
   every host this install may contact, grouped by purpose, with the scopes asked for and granted and when each was last contacted (GET /api/outside,
   outside.py). Admins see the whole install. Read only: connecting and disconnecting are on the Connected sources
   page. admin.js calls SgOutside.load() once it knows the person is an admin. */
"use strict";   // $ and el come from util.js
const SgOutside = (() => {
  const when = t => t ? fmtDT(t * 1000) : "never";   // this person's time zone and format (dates.js)
  const code = t => el("code", {text: t});
  const sub = text => el("h3", {class: "sub", text});
  const dl = rows => el("dl", {}, ...rows.filter(r => r).flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {}, v)]));
  const times = n => n === 1 ? "once" : `${n} times`;
  const none = what => emptyState(what, null, {cls: ""});   // a list with nothing in it says so (util.js, #499: "None yet.")
  const profileOf = e => e.profile_name || (e.profile == null ? (["Provider directory", "Sign-in provider", "Notification server", "Text recognition language"].includes(e.purpose) ? "no profile (the install's)" : "no profile (an admin's test)") : "profile " + e.profile);

  function source(s) {
    const h = s.hosts || {}, v = s.vendor_label || s.vendor;   // its vendor as Set up a source names it (#528: "epic")
    const box = el("div", {class: "out-src"},
      el("h4", {text: s.name + (v ? ` · ${v}` : "")}),
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
    if (!list.length) return none(empty);
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
      sayIn(msg, "Couldn't load the outside connections: " + e.message, true);
      return;
    }
    sayIn(msg, "");   // (#519: a failed load's message stayed beside the list once it loaded)
    // its first sentence the lead; the rest, and whose this lists, behind the heading's ⓘ (#280)
    const [lead, ...rest] = v.nothing_else.split(/(?<=\.) /);
    $("#outsideLead").textContent = lead;
    $("#outsideMore").textContent = rest.join(" ");
    $("#outsideScope").textContent = v.admin ? "You're an admin, so this lists the whole install."
      : "This lists the profiles you own or edit. An admin sees the whole install.";
    const body = $("#outsideBody");
    body.replaceChildren(
      sub("May contact now"),
      v.may_contact.length ? el("ul", {class: "out-hosts"}, ...v.may_contact.map(h => el("li", {}, code(h))))
        : none(v.connected_sources ? "No host: no source is set up or connected, so Sanguity contacts nobody on its own." : "No host: Sanguity contacts nobody on its own."),
      el("p", {class: "muted"}, "A Health Link's host is contacted only when someone pastes the link and confirms it.",
        ...(v.connected_sources ? [" ", el("a", {href: "sources.html#portals", text: "Connect or disconnect sources"}), "."] : [])),
      ...(v.connected_sources ? [sub("Connected sources"),   // (#1096) none of theirs while they're off
        ...(v.sources.length ? v.sources.map(source) : [none("No source set up or connected yet: each is listed here with the hosts it may contact.")])] : []),
      sub("SMART Health Link fetches"),
      contacts(v.health_links, "No Health Link fetched yet: each host is listed once someone pastes a link and confirms it."),
      sub("Health Card issuers' keys"),
      contacts(v.card_keys, "No issuer's key fetched yet: each is listed once a health card's signature is checked with it."),
      ...(v.directory ? [sub("Provider directory"),   // (#992) an admin's alone: the lists Find your provider searches
        el("p", {class: "muted"}, `Epic's and Oracle Health's lists, from ${v.directory.hosts.join(" and ")}: read only when an admin asks, or weekly once one turns that on, in `,
          el("a", {href: "#directory", text: "Connected sources, Provider directory"}), "."),   // (#1153)
        contacts(v.directory.contacts, "Not read yet: each host is listed once the lists are read.")] : []),
      ...(v.sign_in ? [sub("Sign-in provider"),   // (#1109) the install's, an admin's alone
        el("p", {class: "muted"}, "Contacted when someone signs in or links an identity with it, or an admin tests it, in ",
          el("a", {href: "#signin-provider", text: "Accounts and sign-in, Sign-in provider"}), "."),
        contacts(v.sign_in.contacts, "Not contacted yet: each host is listed once someone signs in with it.")] : []),
      ...(v.notifications ? [sub("Notification server"),   // (#1130) the install's own, set by an admin
        el("p", {class: "muted"}, `${v.notifications.hosts.join(", ")}: sent the notifications people turn on, in `,
          el("a", {href: "#notify", text: "Notifications"}), ", saying only which profile and what kind", v.admin ? ["; set in ", el("a", {href: "#gen-notify", text: "General, Notifications"})] : "", "."),
        ...(v.admin ? [contacts(v.notifications.contacts, "Not contacted yet: each contact is listed once a notification or a test is sent.")] : [])] : []),
      ...(v.languages ? [sub("Text recognition languages"),   // (#1129) an admin's alone, while downloading them is on
        el("p", {class: "muted"}, "The tesseract-ocr project's language files, downloaded only when an admin asks for one, in ",
          el("a", {href: "#gen-ocr", text: "General, Text recognition"}), "."),
        contacts(v.languages.contacts, "None downloaded yet: the host is listed once one is.")] : []),
      ...(v.earlier.length ? [sub("Earlier contacts, no longer allowed"), contacts(v.earlier, "")] : []));
  }
  return {load};
})();
