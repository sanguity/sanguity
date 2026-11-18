/* Sanguity · Sharing page: a profile's share links (#47; server/share_links.py), for its owners. Each link lets someone
   without an account read the part of the profile chosen here, until it expires or an owner revokes it. The form
   says plainly, as it's filled in, everything a holder of the link will see. The link's address is shown once.
   Uses call() and when() from sharing.js (loaded after this file; called only once the page has loaded). With open,
   the panel is shown at once, with no button to open it. */
"use strict";
const SL_HOURS = [[1, "1 hour"], [24, "1 day"], [72, "3 days"], [168, "7 days"], [720, "30 days"], [2160, "90 days"]];
const SL_STATE = {active: "working", expired: "expired", revoked: "revoked", maker_not_owner: "stopped (its maker is no longer an owner)"};

function slRange(from, to) {
  return from && to ? `dated ${fmtD(dayTs(from))} to ${fmtD(dayTs(to))}` : from ? `dated ${fmtD(dayTs(from))} or later`
    : to ? `dated up to ${fmtD(dayTs(to))}` : "of any date";
}

function shareLinksPanel(p, {open = false} = {}) {
  const box = el("div", {class: "share-links", hidden: true});
  const msg = el("span", {class: "msg", role: "status"});
  const fail = e => sayIn(msg, e.message, true);
  const list = el("div"), made = el("div");
  let info = null;

  function drawList() {
    const rows = info.links;
    if (!rows.length) {   // New share link, below, is the next step (#199)
      list.replaceChildren(el("p", {class: "muted", text: "No share links yet: each one you make is listed here, with when it was last opened."}));
      return;
    }
    const names = Object.fromEntries(info.sections.map(s => [s.id, s.label]));
    list.replaceChildren(scrollBox({class: "share-scroll", "aria-label": "Share links"}, el("table", {class: "tbl"},
      el("thead", {}, el("tr", {}, ...["Link", "Shows", "Ends", "Opened", ""].map(h => el("th", {text: h})))),
      el("tbody", {}, ...rows.map(l => el("tr", {class: l.state === "active" ? "" : "ended"},
        el("td", {}, el("b", {text: l.label || "Unnamed link"}), el("div", {class: "muted", text: `made by ${l.created_by}, ${when(l.created)}`})),
        el("td", {}, l.sections.map(s => names[s] || s).join(", ") + (l.labs.length ? ` (${l.labs.length} lab${l.labs.length === 1 ? "" : "s"})` : ""),
          el("div", {class: "muted", text: slRange(l.from, l.to)})),
        el("td", {}, l.state === "active" ? when(l.expires) : SL_STATE[l.state],
          l.state === "revoked" ? el("div", {class: "muted", text: `by ${l.revoked_by}, ${when(l.revoked)}`}) : null),
        el("td", {text: l.views ? `${l.views} time${l.views === 1 ? "" : "s"}, last ${when(l.last_viewed)}` : "never"}),
        el("td", {}, l.state === "revoked" || l.state === "expired" ? null : el("button", {class: "btn small", type: "button", text: "Revoke",
          onclick: async () => {
            if (!await ask({title: `Revoke ${l.label ? `“${l.label}”` : "this link"}?`, message: "It stops working at once, for anyone who has it.",
                            action: "Revoke link", danger: true})) return;
            try { await call(`profiles/${p.id}/links/${l.id}`, "DELETE"); sayIn(msg, "Revoked."); await load(); } catch (e) { fail(e); }
          }}))))))));
  }

  function form() {
    const label = el("input", {maxlength: 80, placeholder: "Dr. Rivera, or Mum", "aria-label": "Name for this link"});
    const checks = info.sections.map(s => ({s, input: el("input", {type: "checkbox", value: s.id, checked: ["visits", "results", "medications"].includes(s.id)})}));
    const allLabs = el("input", {type: "radio", name: `sl-labs-${p.id}`, checked: true});
    const someLabs = el("input", {type: "radio", name: `sl-labs-${p.id}`});
    const labChecks = info.labs.map(n => ({n, input: el("input", {type: "checkbox", value: n})}));
    const labBox = el("div", {class: "share-labpick", hidden: true}, ...labChecks.map(c => el("label", {class: "check"}, c.input, " " + c.n)));
    const labsPart = el("fieldset", {class: "share-labs"}, el("legend", {text: "Which labs"}),
      el("label", {class: "check"}, allLabs, " Every lab"),
      el("label", {class: "check"}, someLabs, " Only these:", info.labs.length ? null : el("span", {class: "muted", text: " (this profile has no lab results yet)"})), labBox);
    const yearAgo = isoDay(Date.now() - 365 * 864e5);
    const from = el("input", {type: "date", value: yearAgo, "aria-label": "From"}), to = el("input", {type: "date", "aria-label": "To"});
    const hours = el("select", {"aria-label": "Stops working after"}, ...SL_HOURS.map(([h, t]) => el("option", {value: h, text: t})));
    hours.value = "168";
    const sees = el("div", {class: "share-sees", role: "status", "aria-live": "polite"});
    const go = el("button", {class: "btn primary", type: "submit", text: "Make the link"});

    function chosen() {
      const sections = checks.filter(c => c.input.checked).map(c => c.s.id);
      const labs = sections.includes("results") && someLabs.checked ? labChecks.filter(c => c.input.checked).map(c => c.n) : [];
      return {label: label.value.trim(), sections, labs, from: from.value || null, to: to.value || null, hours: +hours.value};
    }
    function explain() {
      const c = chosen();
      labsPart.hidden = !c.sections.includes("results");
      labBox.hidden = !someLabs.checked;
      const ends = when(Date.now() / 1000 + c.hours * 3600);
      const items = checks.filter(x => x.input.checked).map(x => el("li", {},
        el("b", {text: x.s.label}), x.s.id === "results" && someLabs.checked
          ? ` (only ${c.labs.length ? c.labs.join(", ") : "the labs you tick"}): ${x.s.what}` : `: ${x.s.what}`));
      go.disabled = !c.sections.length || (c.sections.includes("results") && someLabs.checked && !c.labs.length);
      sees.replaceChildren(el("p", {}, el("b", {text: "Anyone who has this link"}),
        ` can read, without signing in, the name “${p.name}” and these records ${slRange(c.from, c.to)}` +
        `${c.to ? "" : " (including records added later)"}${c.sections.includes("appointments") ? "; appointments only from the day it's opened on" : ""}:`),
        c.sections.length ? el("ul", {}, ...items) : el("p", {class: "muted", text: "Choose at least one section."}),
        el("p", {class: "muted", text: `Nothing else: no documents, no other sections or dates, no settings, no other profile, and nothing ` +
          `can be changed. It works until ${ends}, or until an owner revokes it, and stops if you stop being an owner. ` +
          "Each time it's opened is logged in this profile's activity, with the address it came from. Send it only to the person it's for."}));
    }
    const f = el("form", {class: "form share-form", onsubmit: async ev => {
      ev.preventDefault();
      try {
        const j = await call(`profiles/${p.id}/links`, "POST", chosen());
        const url = new URL(j.path, location.href).href;
        const field = el("input", {value: url, readonly: true, "aria-label": "The share link", class: "share-url", onfocus: e => e.target.select()});
        const copy = el("button", {class: "btn", type: "button", text: "Copy", onclick: async () => {
          try { await navigator.clipboard.writeText(url); copy.textContent = "Copied"; } catch (e) { field.select(); } }});
        made.replaceChildren(el("div", {class: "share-made"}, el("p", {}, el("b", {text: "Copy the link now."}),
          " It's shown only this once: Sanguity keeps a fingerprint of it, not the link itself, so it can't show it again."),
          el("div", {class: "row"}, field, copy)));
        f.remove(); await load();
      } catch (e) { fail(e); }
    }},
      el("label", {}, "Name, so you know which link it is (only owners see it)", label),
      el("fieldset", {class: "share-sections"}, el("legend", {text: "What it shows"}),
        ...checks.map(c => el("label", {class: "check"}, c.input, el("span", {}, el("b", {text: c.s.label}), el("span", {class: "muted", text: ` ${c.s.what}`}))))),
      labsPart,
      el("div", {class: "row"}, el("label", {}, "Records from", from), el("label", {}, "to (empty: no end)", to)),
      el("label", {}, "Stops working after", hours),
      sees, el("div", {class: "row"}, go, el("button", {class: "btn", type: "button", text: "Cancel", onclick: () => f.remove()})));
    f.addEventListener("input", explain); f.addEventListener("change", explain);
    explain();
    return f;
  }

  let loading = null;   // the load under way, which New share link waits for (#306)
  function load() {
    return loading = (async () => { info = await call(`profiles/${p.id}/links`); drawList(); })();
  }
  const add = el("button", {class: "btn small", type: "button", text: "New share link", onclick: async () => {
    // the form is built from the sections and labs that come with the links: before they have, wait for them (#306)
    if (!info) { try { await (loading || load()); } catch (e) { fail(e); return; } }
    if (box.querySelector(".share-form")) return;
    made.replaceChildren(); sayIn(msg, "");
    add.after(form());
  }});
  box.append(el("p", {class: "hint", text: "Let someone without an account read the part of this profile you choose. " +
    "Whoever has the link can open it, so treat it like the records themselves."}),
    moreAbout("A share link suits a clinician or a relative. It works until it expires or you revoke it, and each time it's opened " +
      "is logged in this profile's activity, with the address it came from. Sanguity keeps a fingerprint of each link, not the link itself."),
    list, add, made, msg);
  const toggle = el("button", {class: "btn small", type: "button", text: "Share links", "aria-expanded": "false",
    onclick: async () => { box.hidden = !box.hidden; toggle.setAttribute("aria-expanded", !box.hidden); if (!box.hidden) { try { await load(); } catch (e) { fail(e); } } }});
  if (open) { toggle.hidden = true; box.hidden = false; load().catch(fail); }
  return el("div", {class: "share-links-wrap"}, toggle, box);
}
