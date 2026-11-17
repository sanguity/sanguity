/* Sanguity · Sources → Phone and watch (#46, #166; until #166 Settings → Watch and scale): the watch receiver's
   upload address and this profile's secret, for its owners and editors, with when the phone last sent something.
   The secret is shown once, when it's made; after that only its last characters. The Sources page signs in and
   chooses the profile (sources-page.js). */
"use strict";   // $, el and api come from util.js
(() => {
  async function call(path, method = "GET", body) {
    const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
    return j;
  }
  const sayIn = (m, text, bad = false) => { m.textContent = text; m.classList.toggle("bad", bad); };
  let ME = null, PID = null;

  const copyBtn = (text, label) => el("button", {class: "btn small", type: "button", text: "Copy", "aria-label": label,
    onclick: async ev => { try { await navigator.clipboard.writeText(text); ev.target.textContent = "Copied"; } catch (e) { ev.target.textContent = "Select and copy it"; } }});
  async function loadWatch() {
    const box = $("#watch"), p = ME.profiles.find(x => x.id === PID);
    box.hidden = true;
    if (!p || (p.role !== "owner" && p.role !== "editor")) { box.replaceChildren(); return; }
    try { drawWatch(await call(`profiles/${PID}/watch/receiver`)); }
    catch (e) { box.replaceChildren(el("h2", {text: "Phone and watch"}), el("p", {class: "msg bad", text: e.message})); box.hidden = false; }
  }
  /* what isn't set up on the server, in words (#167): admins are pointed to the setup guide, everyone else to them */
  const GUIDE = "the setup guide's “Connecting a phone” section says how";
  function drawWatch(st, secret) {
    const box = $("#watch"), msg = el("span", {class: "msg", role: "status"}), admin = ME.user.is_admin;
    const addrs = st.addresses.length ? st.addresses : [admin ? `(not known yet: ${GUIDE} to set this server's address)` : "(not known yet: ask your admin)"];
    const secretNode = secret
      ? el("div", {}, el("code", {class: "w-secret", id: "watchSecret", text: secret}), " ", copyBtn(secret, "Copy the secret"),
          el("p", {class: "hint", text: "Shown only now: put it in the phone app. Afterwards it's kept hidden; make a new one if it's lost."}))
      : st.has_secret ? el("code", {class: "w-secret", text: st.masked})
      : st.legacy ? el("span", {text: "The server's original receiver secret, from before each profile had its own. Make one for this profile to replace it."})
      : el("span", {class: "muted", text: "None yet"});
    const has = st.has_secret || st.legacy;
    const go = el("button", {class: "btn small" + (has ? "" : " primary"), type: "button", id: "watchRotate", text: has ? "Make a new secret…" : "Make a secret",
      onclick: async () => {
        if (has && !confirm("Make a new secret? The phone using the current one stops uploading until you give it the new one.")) return;
        go.disabled = true;
        try { const r = await call(`profiles/${PID}/watch/receiver/rotate`, "POST", {}); drawWatch(r, r.secret); }
        catch (e) { sayIn(msg, e.message, true); go.disabled = false; }
      }});
    box.replaceChildren(...[   // without the parts left out: replaceChildren would write a null out as text (#175)
      el("h2", {text: "Phone and watch"}),
      el("p", {class: "hint", text: "A phone app sends watch, sleep and smart-scale readings to this profile: Life Dashboard Companion on Android (from Health Connect), or Health Auto Export on iPhone (from Apple Health). Garmin, Fitbit, Withings and other apps come through them, by writing to Health Connect or Apple Health. In the app, set:"}),
      el("dl", {class: "kv w-kv"},
        el("dt", {text: "Last upload"}), el("dd", {id: "watchLast", text: st.last ? fmtWhen(st.last) : "Nothing received yet"}),
        el("dt", {text: "Upload address"}), el("dd", {}, ...addrs.flatMap(a => [el("div", {}, el("code", {class: "w-addr", text: a}), " ", st.addresses.length ? copyBtn(a, "Copy the upload address") : null)])),
        el("dt", {text: "Secret"}), el("dd", {}, secretNode)),
      el("p", {class: "hint", text: "Each profile has its own secret, so each person's phone uploads to their own profile. Readings are split into days in the phone's own time zone."}),
      hae(addrs[0], secret),
      st.ready ? null : el("p", {class: "msg bad", text: "The watch receiver isn't ready for this profile's own secret yet: " +
        (admin ? `it needs a folder for the receiver's secrets on the server (${GUIDE}).` : "ask your admin to finish setting it up.") +
        " Uploads with a secret made here won't be taken until then."}),
      el("div", {class: "row"}, go, msg)].filter(Boolean));
    box.hidden = false;
  }
  /* Health Auto Export's setup (#148): a REST API automation per data type, with the secret in a header. */
  function hae(addr, secret) {
    const header = "Bearer " + (secret || "<the secret>"), li = (...c) => el("li", {}, ...c), b = t => el("b", {text: t});
    return el("div", {class: "w-hae", id: "watchHae"},
      el("h3", {class: "sub", text: "iPhone: Health Auto Export"}),
      el("ol", {class: "w-steps"},
        li("In Health Auto Export (its Premium subscription or trial), open ", b("Automations"), " and add a ", b("REST API"), " automation."),
        li(b("URL"), ": the upload address above (", el("code", {class: "w-addr", text: addr}), ")."),
        li(b("Headers"), ": add one with the key ", el("code", {class: "w-addr", text: "Authorization"}), " and the value ",
          el("code", {class: "w-secret", text: header}), secret ? [" ", copyBtn(header, "Copy the header value")] : "", ": ", b("Bearer"), ", a space, then the secret."),
        li(b("Data type"), ": Health Metrics, ", b("Export format"), ": JSON. Choose step count, walking + running distance, active energy, heart rate, resting heart rate, heart rate variability, blood oxygen saturation, respiratory rate, weight, body fat percentage, lean body mass and sleep analysis."),
        li(b("Time grouping"), ": hours (and keep it: changing it later sends the same steps again in other intervals)."),
        li(b("Sync"), ": hourly or daily, with the date range ", b("Since last sync"), " or ", b("Today"), ". Repeated or overlapping uploads count once."),
        li("Add a second automation the same way with the data type ", b("Workouts"), ".")),
      el("p", {class: "hint", text: "Android: in Life Dashboard Companion, set the upload address and the secret above; it signs each upload with the secret."}));
  }

  (async () => {
    ME = await SgSources.ready;
    if (!ME) return;
    PID = SgSources.pid();
    $("#profile").addEventListener("change", () => { PID = SgSources.pid(); loadWatch(); });
    loadWatch();
  })();
})();
