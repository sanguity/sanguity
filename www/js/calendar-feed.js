/* Sanguity · Sharing page: the calendar feed (#51; server/routes_calendar.py; on Settings until #168). Its own block:
   make a private feed of the profile for your own calendar app, see its address once with setup steps, change how
   much it says, get a new address, or turn it off. Owners also see and can turn off everyone's feeds.
   Follows the page's profile picker (#profile, filled by sharing.js) without changing sharing.js. */
"use strict";   // $, el, api and copyBtn come from util.js; qrShow from qr.js
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

  /* the address's Copy and its QR code, the app's one each (#495): the code behind its button, for a phone's calendar */
  const copy = copyBtn(() => $("#calUrl").value, {what: "the feed's address", sel: $("#calUrl")});
  copy.id = "calCopy";
  $("#calCopy").replaceWith(copy);
  let qr = null;
  const unQr = () => { qr?.btn?.remove(); qr?.fig?.remove(); qr = null; };
  function showSetup(path) {
    const url = new URL(path, location.href.replace(/[^/]*$/, "")).href;
    $("#calUrl").value = url;
    unQr();
    qr = qrShow(url, {what: "the feed's address", caption: "Scan it with the phone's camera to take the address there, then subscribe to it.", field: true});
    if (qr) { copy.after(qr.btn); $("#calSetup .cal-url").after(qr.fig); }
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
    $("#calUrl").value = ""; unQr();
    const f = d.mine;
    $("#calNew").hidden = !!f;
    $("#calHave").hidden = !f;
    if (f) {
      $("#calStatus").textContent = `Your feed is on: made ${when(f.created)}${f.rotated ? `, address changed ${when(f.rotated)}` : ""}. ` +
        `Last fetched by your calendar app: ${when(f.last_fetched)}.`;
      $("#calDetailsNow").value = f.details;
      $("#calLabsNow").checked = f.labs;
      $("#calMedsNow").checked = f.meds;   // medication reminders (#162)
      shown = {details: f.details, labs: f.labs, meds: f.meds};
    }
    const others = d.everyone || [];
    $("#calOthers").hidden = !others.length;
    $("#calList").replaceChildren(...others.map(o => el("tr", {},
      el("td", {text: o.username + (o.user_id === me ? " (you)" : "")}),
      el("td", {text: SAYS[o.details] + (o.labs ? ", with expected labs" : "") + (o.meds ? ", with medication reminders" : "")}),
      el("td", {text: when(o.last_fetched)}),
      el("td", {class: "acts"}, el("button", {class: "btn small", type: "button", text: "Turn off", "aria-label": `Turn off ${o.username}'s feed`,
        onclick: async () => {
          if (!await ask({title: `Turn off ${o.username}'s calendar feed?`, message: "Their calendar app stops getting updates.",
                          action: "Turn off feed", danger: true})) return;
          try { await call(`profiles/${pid}/calendar/${o.id}`, "DELETE"); await load(); say(`${o.username}'s feed is off.`); }
          catch (e) { say(e.message, true); }
        }})))));
  }

  $("#calCreate").onclick = async () => {
    const details = document.querySelector("input[name=calDetails]:checked").value;
    $("#calCreate").disabled = true;
    try {
      const j = await call(`profiles/${pid}/calendar`, "POST", {details, labs: $("#calLabs").checked, meds: $("#calMeds").checked, tz: zone()});
      await load();
      showSetup(j.path);
      say("Your calendar feed is ready. Add it to your calendar app with the address below.");
    } catch (e) { say(e.message, true); }
    $("#calCreate").disabled = false;
  };
  $("#calRotate").onclick = async () => {
    if (!await ask({title: "Get a new address?", message: "The old one stops working at once: you'll need to subscribe again with the new one.",
                    action: "Get new address", danger: true})) return;
    try { const j = await call(`profiles/${pid}/calendar/rotate`, "POST"); await load(); showSetup(j.path); say("New address made; the old one no longer works."); }
    catch (e) { say(e.message, true); }
  };
  $("#calRevoke").onclick = async () => {
    if (!await ask({title: "Turn off your calendar feed?", message: "Its address stops working. Remove the subscription from your calendar app too.",
                    action: "Turn off feed", danger: true})) return;
    try { await call(`profiles/${pid}/calendar`, "DELETE"); await load(); say("Your calendar feed is off."); }
    catch (e) { say(e.message, true); }
  };
  /* what the feed says, changed at once, with an Undo (#272) that puts back what it was if it's still as set here */
  let shown = {};   // as loaded: {details, labs, meds}
  const patch = async body => {
    const p = pid, [k] = Object.keys(body), was = shown[k];
    try { await call(`profiles/${p}/calendar`, "PATCH", body); await load(); }
    catch (e) { say(e.message, true); return; }
    const said = "Saved. Your calendar app shows the change at its next refresh.";
    sayIn(msg, said, false, {say: false});
    toast(said, {action: "Undo", onAction: async () => {
      const now = (await call(`profiles/${p}/calendar`)).mine, back = now && now[k] === body[k];
      if (back) await call(`profiles/${p}/calendar`, "PATCH", {[k]: was});
      if (pid === p) { await load(); say(back ? "Undone: the feed says what it did before." : "The feed was changed since, so it's left as it is."); }
    }});
  };
  $("#calDetailsNow").onchange = () => patch({details: $("#calDetailsNow").value});
  $("#calLabsNow").onchange = () => patch({labs: $("#calLabsNow").checked});
  $("#calMedsNow").onchange = () => patch({meds: $("#calMedsNow").checked});   // (#162)
  $("#calDone").onclick = () => { $("#calSetup").hidden = true; $("#calUrl").value = ""; unQr(); say(""); };

  /* follow the profile picker: sharing.js fills it, then sets its value */
  const sel = $("#profile");
  const pick = () => { const v = +sel.value; if (v && v !== pid) { pid = v; say(""); load(); } };
  sel.addEventListener("change", pick);
  new MutationObserver(pick).observe(sel, {childList: true});
  api("me").then(r => r.ok ? r.json() : null).then(j => { if (j) me = j.user.id; }).catch(() => {});
})();
