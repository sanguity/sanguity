/* Sanguity · Settings → Install, "Outside connections" (#59, #97; the Admin page's from #167, Settings' since #991):
   every host this install may contact, grouped by purpose, with the scopes asked for and granted and when each was last contacted (GET /api/outside,
   outside.py). Admins see the whole install. Read only: connecting and disconnecting are on the Connected sources
   page. admin.js calls SgOutside.load() once it knows the person is an admin.
   (#1167) For admins it's also where outside connections are controlled: a switch per kind and "No outside connections
   at all" over them (install settings; the kinds' own switches elsewhere are the same settings), each host's Test (its
   name, a connection and TLS, through the outbound proxy) and Block, the blocked hosts, the outbound proxy (Needs a
   restart's settings, #1159, saved for the next start) with its password; and for everyone the recent contacts,
   filtered by kind, host and date, which an admin may clear (asked first). Every change asks for the fresh check
   (admin.js fresh, in #outsideReauth); net.py enforces them all. */
"use strict";   // $ and el come from util.js
const SgOutside = (() => {
  const when = t => t ? fmtDT(t * 1000) : "never";   // this person's time zone and format (dates.js)
  const code = t => el("code", {text: t});
  const sub = text => el("h3", {class: "sub", text});
  const dl = rows => el("dl", {}, ...rows.filter(r => r).flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {}, v)]));
  const times = n => n === 1 ? "once" : `${n} times`;
  const none = what => emptyState(what, null, {cls: ""});   // a list with nothing in it says so (util.js, #499: "None yet.")
  const profileOf = e => e.profile_name || (e.profile == null ? (["Provider directory", "Sign-in provider", "Notification server", "Text recognition language", "Update check", "Reachability test"].includes(e.purpose) ? "no profile (the install's)" : "no profile (an admin's test)") : "profile " + e.profile);

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

  const say = (text, bad) => sayIn($("#outsideMsg"), text, bad);
  const put = (key, value) => confirmed(typed => fresh(() => call(`settings/install/${encodeURIComponent(key)}`, "PUT", {value, confirm: typed}), $("#outsideReauth")));
  const btn = (text, onclick, more = {}) => el("button", {type: "button", class: "btn small", text, onclick, ...more});
  const again = async (note, focus) => { await load(); if (note) say(note); if (focus) document.getElementById(focus)?.focus({preventScroll: true}); };

  /* (#1167) a switch per kind, and the master switch over them */
  function switches(sw) {
    const row = (id, name, help, on, key, said) => {
      const box = el("input", {type: "checkbox", role: "switch", id, checked: on ? "" : null, "aria-labelledby": id + "-l", "aria-describedby": id + "-h",
        onchange: async () => {
          try { if (!await put(key, key === "outside.none" ? box.checked : box.checked)) { box.checked = !box.checked; return; } await again(said(box.checked), id); }
          catch (e) { box.checked = !box.checked; say(`Not changed: ${e.message}`, true); }
        }});
      return setRow([el("p", {class: "set-row-name", id: id + "-l", text: name}), el("p", {class: "hint", id: id + "-h", text: help})], el("label", {class: "check switch"}, box));
    };
    return [sub("What may connect"),
      row("outNone", "No outside connections at all", "On: this server contacts nobody, whatever is switched on below; every feature that would says why.", sw.none, "outside.none",
        on => on ? "No outside connections at all: this server contacts nobody." : "Outside connections are as switched below."),
      ...sw.kinds.map(k => row("outK-" + k.kind, k.name, `${k.for} Off: ${k.off}${sw.none ? " (No outside connections at all is on.)" : ""}`, k.on, k.key,
        on => `${k.name}: ${on ? "on" : "off"}.`))];
  }

  /* (#1167) each host: Test and Block; the blocked ones, and blocking one by hand */
  function hosts(v) {
    const blocked = v.switches.blocked, list = v.may_contact.filter(h => !blocked.includes(h));
    const result = el("div", {id: "outTestSaid", role: "status"});
    const block = async (h, on) => {
      try { if (await put("outside.blocked", on ? [...blocked, h] : blocked.filter(x => x !== h))) await again(on ? `${h} is blocked: Sanguity won't contact it.` : `${h} isn't blocked now.`); }
      catch (e) { say(`Not changed: ${e.message}`, true); }
    };
    const test = async (h, b) => {
      result.replaceChildren(el("p", {class: "hint", text: `Testing ${h}…`}));
      try {
        const r = await call("outside/test", "POST", {host: h});
        result.replaceChildren(el("p", {}, el("b", {text: `${h}: `}), r.steps.map(x => `${{name: "its name", connect: "a connection", tls: "TLS"}[x.step]} ${x.ok ? "worked" : "failed"} (${x.said})`).join("; ") + "."));
      } catch (e) { result.replaceChildren(el("p", {class: "msg bad", text: `${h}: ${e.message}`})); }
      b.focus();
    };
    const typed = el("input", {id: "outBlockHost", autocomplete: "off", spellcheck: "false", autocapitalize: "none", placeholder: "fhir.example.org", "aria-describedby": "outBlockH"});
    return [sub("Hosts"),
      list.length ? el("ul", {class: "out-list"}, ...list.map(h => el("li", {}, code(h), " ",
        btn("Test", ev => test(h, ev.target), {"aria-label": `Test ${h}`}), " ", btn("Block", () => block(h, true), {"aria-label": `Block ${h}`})))) : none("No host to test: none is listed above."),
      el("p", {class: "hint", text: "Test looks up the host's name, connects and checks its HTTPS certificate, through the outbound proxy if one is set; nothing is sent beyond that."}),
      result,
      sub("Blocked hosts"),
      blocked.length ? el("ul", {class: "out-list"}, ...blocked.map(h => el("li", {}, code(h), " ", btn("Unblock", () => block(h, false), {"aria-label": `Unblock ${h}`}))))
        : none("None blocked."),
      el("div", {class: "form inline"}, el("label", {for: "outBlockHost", text: "Block a host"}), typed, btn("Block", () => typed.value.trim() && block(typed.value.trim(), true))),
      el("p", {class: "hint", id: "outBlockH", text: "Sanguity refuses a blocked host before anything is sent, whatever else allows it, until it's unblocked."})];
  }

  /* (#1159, #1167) the outbound proxy: Needs a restart's settings, saved for the next start, and its password */
  async function proxy() {
    let rs;
    try { rs = await call("settings/install/restart"); } catch (e) { return [sub("Outbound proxy"), el("p", {class: "msg bad", text: e.message})]; }
    const rows = rs.settings.filter(st => st.group === "Outbound proxy"), pw = rs.proxy_password, env = pw.source === "environment";
    const inputs = rows.map(st => el("input", {id: "outPx-" + st.name, value: st.next ?? st.value, autocomplete: "off", spellcheck: "false", autocapitalize: "none", "aria-describedby": `outPx-${st.name}-h`}));
    const waits = rows.some(st => "next" in st);
    const save = async () => {
      const values = Object.fromEntries(rows.map((st, i) => [st.name, inputs[i].value.trim() || null]));
      try {
        const d = await confirmed(typed => fresh(() => call("settings/install/restart/values", "PUT", {values, confirm: typed}), $("#outsideReauth")));
        if (!d) { say("Not saved: it wasn't confirmed.", true); return; }
        await again(d.restart_needed ? "Saved: it applies once the server restarts (General → Needs a restart, Restart now)." : "Saved.", "outPxSave");
        if (typeof loadGenRestart === "function") loadGenRestart();
      } catch (e) { say(`Not saved: ${e.why || e.message}`, true); }
    };
    const box = el("input", {type: "password", id: "outPxPw", autocomplete: "new-password", "aria-describedby": "outPxPwH", disabled: env});
    const setPw = async value => {
      try { await fresh(() => call("outside/proxy-password", "PUT", {value}), $("#outsideReauth")); await again(value ? "The proxy's password is set." : "The proxy's password is removed.", "outPxPw"); }
      catch (e) { say(`Not changed: ${e.message}`, true); }
    };
    return [sub("Outbound proxy"),
      waits ? el("p", {class: "warn"}, "Saved, and waiting for a restart: ", el("a", {href: "#gen-restart", text: "General → Needs a restart"}), ".") : null,
      ...rows.map((st, i) => setRow([el("label", {for: inputs[i].id, text: st.label}), el("p", {class: "hint", id: `outPx-${st.name}-h`, text: st.help})], inputs[i])),
      setRow([el("label", {for: "outPxPw", text: "Proxy password"}), el("p", {class: "hint", id: "outPxPwH", text: env ? `Managed by the environment (${pw.env}): change it there.`
        : pw.source ? "Set, and never shown. Type a new one to replace it, or Remove it." : "None set. Only if the proxy asks for one; kept for this proxy alone, never shown."})],
        env ? null : el("div", {class: "form inline"}, box, btn("Set password", () => setPw(box.value)), pw.source ? btn("Remove", () => setPw("")) : null)),
      el("div", {class: "row"}, el("button", {type: "button", class: "btn primary", id: "outPxSave", text: "Save proxy", onclick: save}),
        el("span", {class: "hint", text: " It applies at the next restart, and is kept once the server runs healthy with it."}))];
  }

  /* (#1167) recent contacts, filtered by kind, host and date; an admin may clear them (asked first) */
  function history(v) {
    const kinds = v.switches?.kinds || [];
    const kind = el("select", {id: "outLogKind"}, el("option", {value: "", text: "Every kind"}),
      ...kinds.map(k => el("option", {value: k.kind, text: k.name})), el("option", {value: "test", text: "Tests from here"}));
    const host = el("input", {id: "outLogHost", autocomplete: "off", spellcheck: "false", autocapitalize: "none"});
    const from = el("input", {type: "date", id: "outLogFrom"}), to = el("input", {type: "date", id: "outLogTo"});
    const out = el("div", {id: "outLog", "aria-live": "polite"});
    const show = async () => {
      const q = new URLSearchParams({kind: kind.value, host: host.value.trim()});
      if (from.value) q.set("since", Math.floor(new Date(from.value + "T00:00").getTime() / 1000));
      if (to.value) q.set("until", Math.floor(new Date(to.value + "T23:59:59").getTime() / 1000));
      try {
        const r = await call("outside/log?" + q);
        out.replaceChildren(r.contacts.length ? el("ul", {class: "out-list"}, ...r.contacts.map(c => el("li", {}, code(c.host), ` · ${profileOf(c)} · ${c.purpose} · ${when(c.at)}`)))
          : none("No contact matches."));
      } catch (e) { out.replaceChildren(el("p", {class: "msg bad", text: e.message})); }
    };
    const clear = async () => {
      try {
        const r = await confirmed(typed => fresh(() => call("outside/clear", "POST", {confirm: typed}), $("#outsideReauth")));
        if (r) await again(`Cleared ${r.cleared} contact${r.cleared === 1 ? "" : "s"}: it's said in Install activity.`, "outLogClear");
      } catch (e) { say(`Not cleared: ${e.message}`, true); }
    };
    const lbl = (id, text) => el("label", {for: id, text});
    setTimeout(show);
    return [sub("Recent contacts"),
      el("div", {class: "form inline", role: "group", "aria-label": "Filter the contacts"}, lbl("outLogKind", "Kind"), kind, lbl("outLogHost", "Host"), host,
        lbl("outLogFrom", "From"), from, lbl("outLogTo", "To"), to, btn("Show", show)),
      out, v.admin ? el("div", {class: "row"}, btn("Clear history…", clear, {id: "outLogClear"})) : null];
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
    const body = $("#outsideBody"), ctl = v.switches ? [...switches(v.switches), ...await proxy()] : [];
    const back = body.contains(document.activeElement) ? document.activeElement.id : null;
    body.replaceChildren(...present(...ctl,
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
      ...(v.updates ? [sub("Update check"),   // (#1159) an admin's alone, while it's on
        el("p", {class: "muted"}, "GitHub's releases, asked once a day for Sanguity's latest, in ", el("a", {href: "#gen-instance", text: "General, This server"}), "."),
        contacts(v.updates.contacts, "Not asked yet.")] : []),
      ...(v.languages ? [sub("Text recognition languages"),   // (#1129) an admin's alone, while downloading them is on
        el("p", {class: "muted"}, "The tesseract-ocr project's language files, downloaded only when an admin asks for one, in ",
          el("a", {href: "#gen-ocr", text: "General, Text recognition"}), "."),
        contacts(v.languages.contacts, "None downloaded yet: the host is listed once one is.")] : []),
      ...(v.earlier.length ? [sub("Earlier contacts, no longer allowed"), contacts(v.earlier, "")] : []),
      ...(v.switches ? hosts(v) : []), ...history(v)));
    if (back) document.getElementById(back)?.focus({preventScroll: true});
  }
  return {load};
})();
