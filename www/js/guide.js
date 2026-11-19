/* Sanguity · the getting-started guide (#199): guide.html. From a profile with nothing in it to its first records, one
   step at a time: whose records it holds (its name: owners only), the first records, a phone (optional), then the
   Overview. Every step can be skipped, and the step list at the top reaches any of them again. The records and the
   phone come in on the Sources page, as anywhere else: its links carry ?guide=<step>, and that page shows a way back
   here (sources-page.js). Someone with no profile yet makes one in the first step (also guide.html?new); someone
   who can only view a profile is told its owners and editors set it up, with no steps of theirs.

   Signing in to an empty profile of your own (your only one) opens this page until you finish or skip it: the
   per-person setting "guide" ("", "skipped" or "done", server/settings.py), set here. Your menu's Getting started
   opens it any time; so does the records page's empty profile.
     GET   api/profiles/{pid}/imports          whether records have come in (active imports)
     GET   api/profiles/{pid}/watch/receiver   whether a phone is set up
     PATCH api/profiles/{pid}, POST api/profiles   name the profile, or make one
     PUT   api/settings/user/guide             finished or skipped */
"use strict";   // $, el and api come from util.js
(() => {
  const STEPS = [
    {id: "name", title: "Whose records", owner: true},
    {id: "records", title: "First records"},
    {id: "phone", title: "Phone (optional)"},
    {id: "done", title: "Overview"},
  ];
  const q = new URLSearchParams(location.search), making = q.has("new");
  let ME = null, P = null, steps = [], current = null;
  const done = {};   // the steps whose aim is met: records came in, a phone is set up

  async function call(path, method = "GET", body) {
    const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = r.status === 204 ? null : await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((j && typeof j.detail === "string" && j.detail) || (j && Array.isArray(j.detail) ? "Please check the name." : r.status + " " + r.statusText));
    return j;
  }
  const setGuide = v => call("settings/user/guide", "PUT", {value: v}).catch(() => {});   // a failure only means it opens again
  const records = (pid, hash) => `./?profile=${pid}#${hash}`;
  $("#logout").onclick = logOut;   // util.js: asks first about what isn't saved (#391)

  /* ---------------------------------------------------------------- the steps */
  function drawList() {
    $("#guideSteps").replaceChildren(...steps.map((s, i) => {
      const reach = !!P || s.id === "name";   // with no profile yet, only its first step
      const kids = [el("span", {class: "guide-n", "aria-hidden": "true"}, done[s.id] ? icon("check") : String(i + 1)),
        el("span", {text: s.title}), done[s.id] ? el("span", {class: "vh", text: " (done)"}) : null];
      return el("li", {class: [s.id === current ? "cur" : "", done[s.id] ? "done" : ""].join(" ").trim() || null},
        reach && s.id !== current ? el("a", {href: "#" + s.id, onclick: ev => { ev.preventDefault(); show(s.id, true); }}, ...kids)
          : el("span", {"aria-current": s.id === current ? "step" : null}, ...kids));
    }));
  }
  /* show one step; moved: someone chose it (a button, the list), so focus goes to its heading and it's said */
  function show(id, moved = false) {
    const s = steps.find(x => x.id === id) || steps[0];
    current = s.id;
    for (const sec of document.querySelectorAll(".guide-step")) sec.hidden = sec.id !== "step-" + s.id;
    $("#guideSkip").hidden = !P || P.role === "viewer" || s.id === "done";   // nothing left to skip at the end
    drawList();
    viewTitle("Getting started: " + s.title, moved && "Getting started: " + s.title);
    if (location.hash.slice(1) !== s.id) try { history.replaceState(history.state, "", "#" + s.id); } catch (e) {}
    if (moved) {
      const h = $("#step-" + s.id + " h2");
      h.tabIndex = -1;
      h.focus({preventScroll: true});
      window.scrollTo({top: 0, behavior: smoothly()});
    }
  }
  addEventListener("hashchange", () => { if (steps.length && location.hash.slice(1) !== current) show(location.hash.slice(1)); });   // a link to a step
  const next = () => { const i = steps.findIndex(s => s.id === current); show(steps[Math.min(i + 1, steps.length - 1)].id, true); };
  for (const b of document.querySelectorAll("[data-skip], [data-next]")) b.onclick = next;

  /* whose records: the profile's name (owners), or a new profile */
  $("#nameForm").onsubmit = async ev => {
    ev.preventDefault();
    const name = $("#profName").value.trim(), msg = $("#nameMsg");
    if (!name) { sayIn(msg, "Type a name first.", true); return; }
    $("#nameGo").disabled = true;
    try {
      if (!P) {   // a new profile: open the guide on it
        const r = await call("profiles", "POST", {name});
        location.replace(`guide.html?profile=${r.id}#records`);
        return;
      }
      if (name !== P.name) {
        await call(`profiles/${P.id}`, "PATCH", {name});
        P.name = name;
        SgNav.profiles(ME.profiles, P.id);   // the top bar's picker says the new name
        sayIn(msg, "Saved.");
      }
      drawFor();
      next();
    } catch (e) { sayIn(msg, e.message, true); }
    $("#nameGo").disabled = false;
  };
  /* the words and links that name the profile, once it's known (and again after it's renamed) */
  function drawFor() {
    if (!P) return;
    for (const a of document.querySelectorAll(".guide-way")) a.href = `sources.html?profile=${P.id}&guide=${a.dataset.to === "phone" ? "phone" : "records"}#${a.dataset.to}`;
    $("#viewGo").href = records(P.id, "overview");
  }
  /* what's in the profile already: its records and its phone, each step saying so and offering Continue */
  async function status() {
    const [imps, phone] = await Promise.all([call(`profiles/${P.id}/imports`).catch(() => null),
      call(`profiles/${P.id}/watch/receiver`).catch(() => null)]);
    const n = imps ? (imps.imports || []).filter(e => e.state === "active").length : 0;
    done.records = n > 0;
    done.phone = !!(phone && (phone.has_secret || phone.legacy));
    const have = (id, yes, text) => {
      const p = $("#" + id);
      p.hidden = !yes;
      p.replaceChildren(el("span", {class: "guide-tick", "aria-hidden": "true"}, icon("check"), " "), text);
      const sec = p.closest(".guide-step");
      sec.querySelector("[data-next]").hidden = !yes;
      sec.querySelector("[data-skip]").hidden = yes;
    };
    have("recordsHave", done.records, `${P.name} has records now, from ${n} import${n === 1 ? "" : "s"}. Add more any time under Sources.`);
    have("phoneHave", done.phone, "A phone is set up for this profile. Its readings appear on Body and activity once it sends some.");
    $("#doneSays").textContent = done.records
      ? `${P.name} has records now. The Overview shows the latest of each key lab, anything flagged outside its reference range, and what happened most recently.`
      : `${P.name} has no records yet, so the Overview starts empty. It fills in as records come in: Add, in the top bar, brings them in any time.`;
    drawList();
  }
  $("#finish").onclick = async () => {
    $("#finish").disabled = true;
    await setGuide("done");
    location.href = records(P.id, "overview");
  };
  $("#skipGuide").onclick = async () => {
    await setGuide("skipped");
    location.href = P ? records(P.id, "overview") : "./";
  };

  /* ---------------------------------------------------------------- start */
  (async () => {
    let r;
    try { r = await api("me"); } catch (e) { return; }
    if (r.status === 401) { location.replace("login.html"); return; }
    if (!r.ok) return;
    ME = await r.json();
    if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
    if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
    $("#who").textContent = ME.user.username;
    { const w = plainHttpWarning(); if (w) { $("#insecure").textContent = w; $("#insecure").hidden = false; } }
    const pid = making ? null : SgNav.profiles(ME.profiles);   // the top bar's picker (nav.js): the address's, else this browser's last
    if (making) $("#profilePick").hidden = true;
    P = ME.profiles.find(p => p.id === pid) || null;
    $("#profile").addEventListener("change", () => location.replace(`guide.html?profile=${$("#profile").value}`));   // its own steps
    $("#guideHd").hidden = false;
    if (P && P.role === "viewer") {   // no steps of theirs: its owners and editors set it up
      steps = [{id: "view", title: P.name}];
      $("#guideSteps").hidden = true;
      $("#viewSays").textContent = `You can view ${P.name}. Its owners and editors bring in its records and set up its phone, and what they add appears here for you too.`;
      drawFor();
      show("view");
      return;
    }
    steps = !P || P.role === "owner" ? STEPS : STEPS.filter(s => !s.owner);
    if (!P) {   // no profile yet (or making another): its name first, and nothing to skip to
      $("#nameTitle").textContent = "Whose records will your profile hold?";
      $("#nameGo").textContent = "Create the profile";
      $("#nameForm [data-skip]").hidden = true;
      $("#guideTitle").textContent = making && ME.profiles.length ? "A new profile" : "Getting started";
    } else {
      $("#profName").value = P.name;
    }
    drawFor();
    show(location.hash.slice(1));
    if (P) await status();
  })();
})();
