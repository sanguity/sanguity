/* Sanguity · Sources → Phone and watch (#46, #166, #284; until #166 Settings → Watch and scale): setting up the phone
   app that uploads to the watch receiver, for the profile's owners and editors. An iPhone | Android switch, then one
   card per step, each value (the upload address, the secret, the header) with Copy and its QR code (qr.js: scanned
   with the phone's camera, the text reaches the phone the app is on). The secret is shown once, when it's made; after
   that only its last characters. Above the steps, a live check: "Waiting for the first upload…", asking the server
   every few seconds while this section is in sight, turns to "Received just now" when the phone's upload arrives.
   The Sources page signs in and chooses the profile (sources-page.js). */
"use strict";   // $, el, api, ask, sayIn, announce, icon, aboutWords, copyBtn come from util.js; qrShow from qr.js; fmtWhen, fmtD from dates.js
(() => {
  async function call(path, method = "GET", body, opts = {}) {
    const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body), ...opts});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
    return j;
  }
  let ME = null, PID = null, ST = null, SECRET = null, gen = 0, timer = 0;
  const OS = {iphone: "iPhone", android: "Android"}, OS_KEY = "sg-phone-os";
  let os = (() => { try { const v = localStorage.getItem(OS_KEY); if (OS[v]) return v; } catch (e) { /* no storage: guessed */ }
    return /Android/i.test(navigator.userAgent) ? "android" : "iphone"; })();

  async function loadWatch() {
    const box = $("#watch"), p = ME.profiles.find(x => x.id === PID), mine = ++gen;
    clearTimeout(timer);
    box.hidden = true; SECRET = null;
    if (!p || (p.role !== "owner" && p.role !== "editor")) { box.replaceChildren(); return; }
    try { const st = await call(`profiles/${PID}/watch/receiver`); if (mine === gen) drawWatch(st); }
    catch (e) { box.replaceChildren(el("h2", {text: "Phone and watch"}), el("p", {class: "msg bad", role: "alert", text: e.message})); box.hidden = false; }
  }

  /* ---- the live check: what the server last heard from the phone, kept up to date while it can be seen */
  const ago = s => { const d = Date.now() / 1000 - s;
    return d < 60 ? "just now" : d < 3600 ? `${Math.floor(d / 60)} min ago` : d < 86400 ? `${Math.floor(d / 3600)} h ago`
      : d < 7 * 86400 ? `${Math.floor(d / 86400)} day${d < 2 * 86400 ? "" : "s"} ago` : "on " + fmtD(s * 1000); };
  // waiting: a secret and nothing received with it yet (the receiver takes nothing signed with an old one)
  const waiting = () => (ST.has_secret || ST.legacy) && (!ST.last || (ST.created && ST.last < ST.created));
  function liveLine() {
    const line = $("#watchLive");
    if (!line) return;
    const wait = waiting(), got = !wait && ST.last;
    line.className = "w-live" + (wait ? " wait" : got ? " got" : "");
    line.replaceChildren(...present(got ? icon("check") : null, el("span", {id: "watchLast", title: got ? fmtWhen(ST.last) : null,
      text: wait ? (ST.last ? "Waiting for the first upload with the new secret…" : "Waiting for the first upload…")
        : got ? `Received ${ago(ST.last)}` : "Nothing received yet: make a secret below, then set up the phone app."})));
  }
  const inSight = () => document.visibilityState === "visible" && ($("#watch").checkVisibility?.() ?? !!$("#watch").offsetParent);
  function poll(now = false) {
    clearTimeout(timer);
    if (!ST || !(ST.has_secret || ST.legacy)) return;
    const mine = gen;
    timer = setTimeout(async () => {
      if (mine !== gen) return;
      if (inSight() && navigator.onLine !== false) {
        try {
          const st = await call(`profiles/${PID}/watch/receiver`, "GET", undefined, background());   // (#1063: not use)
          if (mine !== gen) return;
          const before = ST.last;
          ST = {...ST, last: st.last, ready: st.ready};
          if (st.last && st.last !== before) announce("The phone's upload arrived: received just now.");
        } catch (e) { /* tried again next time */ }
        liveLine();
      }
      poll();
    }, now ? 0 : waiting() ? 5000 : 30000);
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") poll(true); });
  document.addEventListener("sources:group", e => { if (e.detail === "phone") poll(true); });

  /* ---- a value to give the phone app: its name, the value, and for what (the upload address...) Copy and its QR code
     behind a button, each named for it: the app's one Copy (util.js copyBtn) and one QR code (qr.js qrShow, #495) */
  function value(name, text, {what, copy = !!what, qr = false, cls = "w-addr", id} = {}) {
    const code = el("code", {class: cls, id, text});
    const q = qr ? qrShow(text, {what, caption: "Scan it with the phone's camera to copy it there."}) : null;
    return el("div", {class: "copy-val"}, el("span", {class: "copy-val-name", text: name}),
      el("div", {class: "copy-val-row"}, code, copy ? copyBtn(text, {what, sel: code}) : null, q?.btn), q?.fig);
  }
  const b = t => el("b", {text: t});
  const para = (...c) => el("p", {}, ...c);

  /* what isn't set up on the server, in words (#167): admins are pointed to the setup guide, everyone else to them */
  const GUIDE = "the setup guide's “Connecting a phone” section says how";
  const address = () => ST.addresses.length ? [...ST.addresses.map(a => value("Upload address", a, {what: "the upload address", qr: true})), local()]
    : el("p", {class: "msg bad", text: ME.user.is_admin ? `The upload address isn't known yet: ${GUIDE} to set this server's address.` : "The upload address isn't known yet: ask your admin."});
  /* (#1103) an address naming this computer itself (localhost, 127.0.0.1, ::1): a phone that's given it reaches only
     itself, so it's said, with where the server's own address is set */
  const LOCAL = h => /^(localhost|\[?::1\]?)$/i.test(h) || h.startsWith("127.");
  function local() {
    const host = a => { try { return new URL(a).hostname; } catch (e) { return ""; } };
    if (!ST.addresses.some(a => LOCAL(host(a)))) return null;
    return el("p", {class: "msg bad", id: "watchLocal"}, icon("triangle-alert", {label: "Warning:"}), " This address names this computer itself, so a phone can't reach it. ",
      ME.user.is_admin ? "Set the address the phone can reach (this server's name or address on your network) in Settings → Install → General → Host, then open this page again."
        : "Ask your admin to set the address the phone can reach.");
  }
  /* the secret's step: made here, shown once */
  function secret() {
    const msg = el("span", {class: "msg", role: "status"}), has = ST.has_secret || ST.legacy;
    const go = el("button", {class: "btn" + (has ? "" : " primary"), type: "button", id: "watchRotate", text: has ? "Make a new secret…" : "Make a secret",
      onclick: async () => {
        if (has && !await ask({title: "Make a new secret?", message: "The phone using the current one stops uploading until you give it the new one.",
                               action: "Make new secret", danger: true})) return;
        go.disabled = true;
        try { const r = await call(`profiles/${PID}/watch/receiver/rotate`, "POST", {}); SECRET = r.secret; drawWatch(r); $("#watch button[aria-label=\"Copy the secret\"]")?.focus(); }
        catch (e) { sayIn(msg, e.message, true); go.disabled = false; }
      }});
    return present(
      SECRET ? value("Secret", SECRET, {what: "the secret", qr: true, cls: "w-secret", id: "watchSecret"})
        : ST.has_secret ? value("Secret", ST.masked, {cls: "w-secret"})
        : ST.legacy ? para("This profile uses the server's original receiver secret, from before each profile had its own. Make one for this profile to replace it.")
        : para("Each profile has its own, so each person's phone uploads to their own profile."),
      SECRET ? el("p", {class: "hint", text: "Shown only now: give it to the phone app. Afterwards it's kept hidden; make a new one if it's lost."})
        : ST.has_secret ? el("p", {class: "hint", text: "Shown only when it's made. Lost it, or setting up another phone? Make a new one, and give it to the phone app."}) : null,
      el("div", {class: "row"}, go, msg));
  }
  const step = (title, ...body) => el("li", {class: "step"}, el("h3", {class: "step-hd", text: title}), ...body);
  const secretStep = () => step(ST.has_secret || ST.legacy ? "The secret" : "Make a secret", ...secret());
  const testStep = how => step("Send a test upload", para(how, " The check at the top of this card turns to ", b("Received"), " when it arrives."));

  const STEPS = {
    // Health Auto Export (#148): a REST API automation per data type, the secret in a header
    iphone: () => [
      step("Get Health Auto Export", para("On the iPhone, from the App Store. Sending to a server takes its Premium subscription or its trial.")),
      secretStep(),
      step("Add a REST API automation", para("In Health Auto Export, open ", b("Automations"), ", add one and choose ", b("REST API"), ". Its ", b("URL"), " is this upload address:"), address()),
      step("Put the secret in a header", para("Under ", b("Headers"), ", add one with this key and value: ", b("Bearer"), ", a space, then the secret."),
        value("Key", "Authorization", {what: "the header key"}),
        SECRET ? value("Value", "Bearer " + SECRET, {what: "the header value", qr: true, cls: "w-secret"}) : value("Value", "Bearer <the secret>", {cls: "w-secret"})),
      step("Choose what it sends, and when", para(b("Data type"), ": Health Metrics. ", b("Export format"), ": JSON. Choose step count, walking + running distance, active energy, heart rate, resting heart rate, heart rate variability, blood oxygen saturation, respiratory rate, VO2 max, weight, body fat percentage, lean body mass, sleep analysis, blood pressure and body temperature."),
        para(b("Time grouping"), ": hours, and keep it: changing it later sends the same steps again in other intervals. ",
          b("Sync"), ": hourly or daily, with the date range ", b("Since last sync"), " or ", b("Today"), ". Repeated or overlapping uploads count once.")),
      testStep("Start the automation by hand in the app, or wait for its first sync."),
      step("Add the others you need", para("Each the same way, with the same address and header:"),
        el("ul", {class: "w-more"},
          el("li", {}, b("Workouts"), ", with the workouts' heart rate data included (for heart rate zones)."),
          el("li", {}, "For a glucose meter or a continuous glucose monitor, one with only ", b("blood glucose"), " and no time grouping, so each reading comes on its own."),   // blood glucose (#181)
          el("li", {}, "For a watch's ECG recordings and irregular rhythm notifications, one with the data type ", b("ECG"), " and one with ", b("Heart Rate Notifications"), ", synced daily since the last sync (a recording is about 1 MB as the app sends it)."))),   // #182
    ],
    // Life Dashboard: Health Connect's data, each upload signed with the secret
    android: () => [
      step("Get Life Dashboard", para("Life Dashboard (formerly Life Dashboard Companion), on the Android phone, from ",   // (#1103) where it's found: F-Droid, not Google Play
        el("a", {href: "https://f-droid.org/packages/com.owen282000.lifedashboard/", target: "_blank", rel: "noopener", id: "watchStore"}, "F-Droid"),
        ". It reads Health Connect: let it read what you'd like sent, such as steps, heart rate, sleep, weight, blood pressure and glucose.")),
      secretStep(),
      step("Give it the address and the secret", para("In the app, set the upload address below and the secret from step 2. It signs each upload with the secret, so only this profile takes it."),
        address()),
      testStep("Start a sync in the app, or wait for its next one."),
    ],
  };
  function steps() {
    return el("ol", {class: "steps", id: "watchSteps", role: "list", "aria-label": `Setting up an ${OS[os]}`}, ...STEPS[os]());
  }

  function drawWatch(st) {
    const box = $("#watch"), admin = ME.user.is_admin;
    ST = st;
    const pick = el("div", {class: "seg w-os", role: "group", "aria-labelledby": "watchOsLbl"}, ...Object.entries(OS).map(([k, t]) =>
      el("button", {type: "button", "data-os": k, "aria-pressed": k === os, text: t, onclick: ev => {
        os = k;
        try { localStorage.setItem(OS_KEY, k); } catch (e) { /* remembered for this visit only */ }
        for (const x of pick.children) x.setAttribute("aria-pressed", x === ev.currentTarget);
        $("#watchSteps").replaceWith(steps());
      }})));
    box.replaceChildren(...present(
      el("h2", {text: "Upload from the phone"}),   // (the group, Phone and watch, holds Cycle tracking too: its title is the h1, #409)
      el("p", {class: "hint", text: "Health Auto Export (iPhone) or Life Dashboard (Android) sends watch, sleep and scale readings here."}),
      aboutWords("Health Auto Export reads Apple Health, and Life Dashboard reads Health Connect. Garmin, Fitbit, Withings and other apps come through them, by writing to Apple Health or Health Connect.",
        "Each profile has its own secret, so each person's phone uploads to their own profile. Readings are split into days in the phone's own time zone."),
      el("p", {class: "w-live", id: "watchLive"}),
      st.ready ? null : el("p", {class: "msg bad", text: "The watch receiver isn't ready for this profile's own secret yet: " +
        (admin ? `it needs a folder for the receiver's secrets on the server (${GUIDE}).` : "ask your admin to finish setting it up.") +
        " Uploads with a secret made here won't be taken until then."}),
      el("div", {class: "w-pick"}, el("span", {id: "watchOsLbl", text: "Set up on"}), pick),
      steps()));
    liveLine();
    box.hidden = false;
    poll();
  }

  (async () => {
    ME = await SgSources.ready;
    if (!ME) return;
    PID = SgSources.pid();
    $("#profile").addEventListener("change", () => { PID = SgSources.pid(); loadWatch(); });
    loadWatch();
  })();
})();
