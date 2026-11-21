/* Sanguity · the getting-started guide (#199): guide.html. From a profile with nothing in it to its first records, one
   step at a time: whose records it holds (its name: owners only), the first records, a phone (optional), then the
   Overview. The steps are one line at the top, each marked done as its aim is met (#290), and any of them can be
   reached from it again; the guide opens at the first that isn't done, and every step but the first can be skipped.
   Every way a profile is made asks its name (setup, or this guide's first step, where New profile leads), so a
   profile that's here has its first step done: a fresh install is asked it once, in setup, and the step here only
   renames it. An export is uploaded in the records step itself, with Sources' drop target (import.js, #281), through
   to its review and Save; the other ways and the phone are on the Sources page: its links carry ?guide=<step>, and
   that page shows a way back here (sources-page.js). Someone with no profile yet makes one in the first step (also guide.html?new); someone
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
  const done = {};   // the steps whose aim is met: the profile is named (it's here), records came in, a phone is set up

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
    const h1 = $("#guideTitle").textContent, t = h1 === "Getting started" ? `${h1}: ${s.title}` : h1;   // "A new profile" as its h1 (#531)
    viewTitle(t, moved && t);
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
    if (P && name === P.name) { next(); return; }   // done already: on to the next step
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
        nameGo();
      }
      drawFor();
      next();
    } catch (e) { sayIn(msg, e.message, true); }
    $("#nameGo").disabled = false;
  };
  /* the name step's button: Continue while the name is the profile's, Save and continue once it's changed */
  const nameGo = () => { if (P) $("#nameGo").textContent = $("#profName").value.trim() === P.name ? "Continue" : "Save and continue"; };
  $("#profName").addEventListener("input", nameGo);
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
    // the words don't name the profile: a name such as "My records" (the one the server's own records came in as)
    // made "My records has records now" (#290)
    have("recordsHave", done.records, `The records are in, from ${n} import${n === 1 ? "" : "s"}. Add more any time under Sources.`);
    have("phoneHave", done.phone, "A phone is set up for this profile. Its readings appear on Body and activity once it sends some.");
    $("#doneSays").textContent = done.records
      ? "The records are in. The Overview shows the latest of each key lab, anything flagged outside its reference range, and what happened most recently."
      : "There are no records yet, so the Overview starts empty. It fills in as records come in: Add, in the top bar, brings them in any time.";
    drawList();
  }
  /* the first records' drop target (#281, import.js): once one is saved, the step says the profile has records, and
     once it's undone (its toast's Undo, #508), it says so no longer: the steps read again whenever the page is told
     (import-jobs.js told). A drop anywhere on this page comes here, the step shown first (drop.js) */
  function dropHere() {
    const t = dropTarget({pid: () => P.id, canEdit: () => P.role === "owner" || P.role === "editor"});
    $("#guideDrop").replaceWith(t.el);
    SgDrop.here(t, () => { if (current !== "records") show("records"); });
  }
  document.addEventListener("sg:imports", ev => { if (P && ev.detail.pid === P.id) ev.detail.wait(status()); });
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
      $("#nameGo").textContent = "Create a profile";
      $("#guideTitle").textContent = making && ME.profiles.length ? "A new profile" : "Getting started";
    } else {
      $("#profName").value = P.name;
      done.name = true;   // named when it was made (setup asked): renamed here if wanted, never asked again (#290)
      nameGo();
      dropHere();
      await status();
    }
    drawFor();
    // the step in the address (a link to it, the way back from Sources), else the first not done yet
    const asked = location.hash.slice(1);
    show(steps.some(s => s.id === asked) ? asked : (steps.find(s => !done[s.id]) || steps[steps.length - 1]).id);
  })();
})();
