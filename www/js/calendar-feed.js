/* Sanguity · Sharing page: the calendar feed (#51; server/routes_calendar.py; on Settings until #168). Its own block:
   make a private feed of the profile for your own calendar app, see its address once with setup steps, change how
   much it says, get a new address, or turn it off. Owners also see and can turn off everyone's feeds.
   Follows the page's profile picker (#profile, filled by sharing.js) without changing sharing.js. */
"use strict";   // $, el and api come from util.js
(() => {
  let pid = null, me = null;
  const card = $("#calendar"), msg = $("#calMsg");
  const say = (text, bad = false) => sayIn(msg, text, bad);   // shown and said (util.js, #209)
  const zone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch (e) { return null; } };
  const when = t => t ? fmtDT(t * 1000) : "not yet";   // this person's time zone and format (dates.js)
  const SAYS = {minimal: "Minimal", full: "Full"};

  async function call(path, method = "GET", body) {
    const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = r.status === 204 ? null : await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
    return j;
  }

  /* where a calendar app elsewhere could reach this address, or why it may not */
  function reach() {
    const h = location.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    const local = h === "localhost" || h.endsWith(".localhost") || h.endsWith(".local") || h.endsWith(".lan") ||
      h.endsWith(".home.arpa") || h.endsWith(".internal") || !h.includes(".") && !h.includes(":") ||
      /^(10|127)\.|^192\.168\.|^172\.(1[6-9]|2\d|3[01])\.|^169\.254\./.test(h) || h === "::1" || /^f[cd]/.test(h);
    const notes = [];
    if (location.protocol === "http:") notes.push("This address uses plain HTTP, so the feed and its token travel unencrypted. Put Sanguity behind HTTPS first if your calendar fetches it over the internet.");
    if (local) notes.push("This server's address only works on your own network (or tailnet): your phone's calendar updates only while it's there, and Google Calendar and Outlook on the web can't reach it at all.");
    return notes.join(" ");
  }

  function showSetup(path) {
    const url = new URL(path, location.href.replace(/[^/]*$/, "")).href;
    $("#calUrl").value = url;
    const r = reach();
    $("#calReach").textContent = r;
    $("#calReach").hidden = !r;
    $("#calSetup").hidden = false;
    $("#calUrl").focus();
    $("#calUrl").select();
  }

  async function load() {
    await datesReady();   // times below in this person's zone and format (dates.js; read once per page)
    if (!pid) return;
    let d;
    try { d = await call(`profiles/${pid}/calendar`); }
    catch (e) { card.hidden = true; return; }
    card.hidden = false;
    $("#calSetup").hidden = true;
    $("#calUrl").value = "";
    const f = d.mine;
    $("#calNew").hidden = !!f;
    $("#calHave").hidden = !f;
    if (f) {
      $("#calStatus").textContent = `Your feed is on: made ${when(f.created)}${f.rotated ? `, address changed ${when(f.rotated)}` : ""}. ` +
        `Last fetched by your calendar app: ${when(f.last_fetched)}.`;
      $("#calDetailsNow").value = f.details;
      $("#calLabsNow").checked = f.labs;
    }
    const others = d.everyone || [];
    $("#calOthers").hidden = !others.length;
    $("#calList").replaceChildren(...others.map(o => el("tr", {},
      el("td", {text: o.username + (o.user_id === me ? " (you)" : "")}),
      el("td", {text: SAYS[o.details] + (o.labs ? ", with expected labs" : "")}),
      el("td", {text: when(o.last_fetched)}),
      el("td", {class: "acts"}, el("button", {class: "btn small", type: "button", text: "Turn off", "aria-label": `Turn off ${o.username}'s feed`,
        onclick: async () => {
          if (!confirm(`Turn off ${o.username}'s calendar feed? Their calendar app stops getting updates.`)) return;
          try { await call(`profiles/${pid}/calendar/${o.id}`, "DELETE"); await load(); say(`${o.username}'s feed is off.`); }
          catch (e) { say(e.message, true); }
        }})))));
  }

  $("#calCreate").onclick = async () => {
    const details = document.querySelector("input[name=calDetails]:checked").value;
    $("#calCreate").disabled = true;
    try {
      const j = await call(`profiles/${pid}/calendar`, "POST", {details, labs: $("#calLabs").checked, tz: zone()});
      await load();
      showSetup(j.path);
      say("Your calendar feed is ready. Add it to your calendar app with the address below.");
    } catch (e) { say(e.message, true); }
    $("#calCreate").disabled = false;
  };
  $("#calRotate").onclick = async () => {
    if (!confirm("Get a new address? The old one stops working at once: you'll need to subscribe again with the new one.")) return;
    try { const j = await call(`profiles/${pid}/calendar/rotate`, "POST"); await load(); showSetup(j.path); say("New address made; the old one no longer works."); }
    catch (e) { say(e.message, true); }
  };
  $("#calRevoke").onclick = async () => {
    if (!confirm("Turn off your calendar feed? Its address stops working; remove the subscription from your calendar app too.")) return;
    try { await call(`profiles/${pid}/calendar`, "DELETE"); await load(); say("Your calendar feed is off."); }
    catch (e) { say(e.message, true); }
  };
  const patch = async body => {
    try { await call(`profiles/${pid}/calendar`, "PATCH", body); await load(); say("Saved. Your calendar app shows the change at its next refresh."); }
    catch (e) { say(e.message, true); }
  };
  $("#calDetailsNow").onchange = () => patch({details: $("#calDetailsNow").value});
  $("#calLabsNow").onchange = () => patch({labs: $("#calLabsNow").checked});
  $("#calCopy").onclick = async () => {
    const box = $("#calUrl");
    try { await navigator.clipboard.writeText(box.value); say("Address copied."); }
    catch (e) { box.focus(); box.select(); say("Select the address and copy it (Ctrl+C or ⌘C)."); }
  };
  $("#calDone").onclick = () => { $("#calSetup").hidden = true; $("#calUrl").value = ""; say(""); };

  /* follow the profile picker: sharing.js fills it, then sets its value */
  const sel = $("#profile");
  const pick = () => { const v = +sel.value; if (v && v !== pid) { pid = v; say(""); load(); } };
  sel.addEventListener("change", pick);
  new MutationObserver(pick).observe(sel, {childList: true});
  api("me").then(r => r.ok ? r.json() : null).then(j => { if (j) me = j.user.id; }).catch(() => {});
})();
