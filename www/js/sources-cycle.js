/* Sanguity · Sources → Phone and watch → Cycle tracking (#183; server/cycle.py, cycle_tracking.py, routes_cycle.py).
   Off for every profile until one of its owners turns it on here. Its owners and editors see this card (editors read
   it: only owners turn it on or off, or delete what's kept); viewers don't see it at all, and aren't told whether it's
   on. The card says plainly what's kept, who sees it, where it never goes, and what deleting reaches and what it
   can't. The Sources page signs in and chooses the profile (sources-page.js). */
"use strict";   // $, el, api, ask, sayIn, aboutWords, dangerZone come from util.js
(() => {
  async function call(path, method = "GET", body) {
    const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
    return j;
  }
  let ME = null, PID = null, SHOWN = null;   // SHOWN: the status drawn
  const day = t => fmtD(t * 1000);
  const toState = () => { const now = $("#cycleState"); now.tabIndex = -1; now.focus(); };   // the line saying what it is now
  const KEPT = "Periods, flow, ovulation tests, cervical mucus and spotting";
  const WHO = "Only this profile's owners and editors see them, on the Timeline (a lane of their own) and, when you tick it, on the Labs charts. Viewers don't, and aren't told cycle tracking is on.";
  const NEVER = "They're never in a share link. An export or a visit summary has them only if you tick them in, each time.";
  const SEX = "Sexual activity is never kept, on or off: it's taken out of every upload before anything is stored, as nothing here needs it.";
  const WORDS = "Sanguity shows what the phone app recorded. It doesn't predict periods or fertile days, or say what a record means.";

  async function load() {
    const box = $("#cycle"), p = ME.profiles.find(x => x.id === PID);
    box.hidden = true; box.replaceChildren();
    if (!p || (p.role !== "owner" && p.role !== "editor")) return;
    try { draw(await call(`profiles/${PID}/cycle`)); }
    catch (e) { box.replaceChildren(el("h2", {text: "Cycle tracking"}), el("p", {class: "msg bad", role: "alert", text: e.message})); box.hidden = false; }
  }
  /* what deleting reaches, and what it can't (said before turning it on, and while it's on), behind a disclosure named
     for it (#494): the card's help is a line; in full in the danger zone, where it's deleted (#495) */
  const reach = () => el("details", {class: "more cy-reach"}, el("summary", {text: "What deleting reaches"}), reachList());
  const reachList = () => el("ul", {class: "cy-list"},
      el("li", {text: "Deleted: what the watch receiver keeps (its database, every upload it archived, rewritten without them, and its summary for this page). Sanguity's own database never holds the records."}),
      el("li", {text: "Not reached: backups of the server or of the receiver's folder made before you delete. A backup can't be rewritten: it keeps what it had until it's deleted itself."}),
      el("li", {text: "Not reached: exports and visit summaries already downloaded with them ticked in, and an Apple Health export you uploaded (it's kept as uploaded and may hold cycle records Sanguity doesn't read; undo that import under Import history to delete it)."}),
      el("li", {text: "Not reached: your phone. Health Connect or Apple Health, and the app that sends them, keep their own."}));
  /* deleting what's kept, or turning it off (which deletes it too): neither can be undone, so they're in the danger zone
     (util.js dangerZone, #495), as deleting a profile or removing an account is: each button opens it saying what goes
     and what deleting reaches, and goes ahead once the profile's name is typed exactly */
  function cycleZone(name, msg) {
    let off = false;
    const z = dangerZone({title: "Delete cycle data", hint: "What's kept for this profile goes for good, the receiver's copies too.", action: "Delete all",
      says: () => [off ? "Turning it off deletes everything kept for this profile, and from then on cycle records are left out of every upload again."
        : "Everything kept for this profile is deleted: the receiver's database, every upload it archived, and what imported Apple Health exports brought. " +
          "Cycle tracking stays on, so new records from the phone are kept again.",   // #223
        el("p", {}, el("b", {text: "What deleting reaches"})), reachList()],
      run: async typed => {
        if (typed.trim() !== name) throw new Error(`Type ${name} exactly.`);
        changed(await call(off ? `profiles/${PID}/cycle` : `profiles/${PID}/cycle/delete`, off ? "PUT" : "POST", off ? {on: false} : {}));
        toState();   // (the card drawn again: focus on what it says now)
        sayIn($("#cycleMsg"), off ? "Cycle tracking is off. What was kept is being deleted." : "Deleting what was kept.");
      },
      pick: el("div", {class: "row"})});
    const body = z.node.querySelector(".dz-body"), go = body.querySelector(".btn.danger");
    const opener = (id, text, isOff, action) => el("button", {class: "btn", type: "button", id, text, "aria-expanded": "false", "aria-controls": body.id, onclick: ev => {
      const again = !body.hidden && off === isOff;
      for (const b of z.node.querySelectorAll("[aria-controls]")) b.setAttribute("aria-expanded", "false");
      if (again) { z.hide(); return; }
      off = isOff; go.textContent = action;
      z.show(name); ev.currentTarget.setAttribute("aria-expanded", "true");
    }});
    z.node.querySelector(".row").append(opener("cycleDelete", "Delete all cycle data…", false, "Delete all cycle data"), opener("cycleOff", "Turn off…", true, "Turn off and delete"));
    return z.node;
  }

  /* the card drawn as it is now, and the profile's open reviews (Upload files') read again, as a save does (#530:
     turned off, one still offered the cycle records it would leave out) */
  function changed(st) {
    draw(st);
    window.sanguityImport?.reviewsAgain(PID);
  }
  function draw(st) {
    const box = $("#cycle"), msg = el("span", {class: "msg", id: "cycleMsg", role: "status"}), owner = st.can_change;
    let zone = null;
    const parts = [el("h2", {text: "Cycle tracking"})];
    const send = async (path, method, body, btn) => {
      btn.disabled = true;
      try {
        changed(await call(path, method, body));
        if (!box.contains(document.activeElement)) toState();   // (#534: its button gone)
        sayIn($("#cycleMsg"), method === "PUT" ? (body.on ? "Cycle tracking is on." : "Cycle tracking is off. What was kept is being deleted.") : "Deleting what was kept.");
      }
      catch (e) { sayIn(msg, e.message, true); btn.disabled = false; }
    };
    if (!st.on) {
      parts.push(el("p", {class: "hint", id: "cycleState", text: `Off. ${KEPT} from the phone apps are left out of every upload before anything is kept.`}),
        aboutWords(`Turned on, ${KEPT.toLowerCase()} from Health Connect (Life Dashboard Companion) or Apple Health (Health Auto Export) are kept for this profile. ${WHO}`, NEVER, SEX, WORDS));
      if (owner) {
        const go = el("button", {class: "btn", type: "button", id: "cycleOn", text: "Turn on cycle tracking…", disabled: !st.ready || null, onclick: async () => {
          if (!await ask({title: "Turn on cycle tracking?", action: "Turn on", message: [
            `${KEPT} the phone sends from now on are kept for this profile, encrypted like the rest of its records.`, WHO, NEVER, SEX,
            "You can delete all of it here at any time, and turning it off deletes it too."]})) return;
          send(`profiles/${PID}/cycle`, "PUT", {on: true}, go);
        }});
        parts.push(el("div", {class: "row"}, go, msg));
        if (!st.ready) parts.push(el("p", {class: "msg bad", text: "The watch receiver can't be told about this yet: its secrets folder isn't set up on the server, so it would leave cycle records out anyway. " +
          (ME.user.is_admin ? "The setup guide's “Connecting a phone” section says how." : "Ask your admin to finish setting it up.")}));
      } else parts.push(el("p", {class: "hint", text: "Only the profile's owners can turn it on."}));
    } else {
      parts.push(el("p", {class: "hint", id: "cycleState", text: onWords(st)}),
        aboutWords(WHO, NEVER, SEX, WORDS),
        el("h3", {class: "sub", text: "Sending them from the phone"}),
        el("ul", {class: "cy-list"},
          el("li", {text: "Android: in Health Connect, let Life Dashboard Companion read cycle tracking (menstruation, ovulation tests, cervical mucus, spotting). It sends them with the rest."}),
          el("li", {text: "iPhone: in Health Auto Export, add a REST API automation like the others, with Cycle Tracking's menstrual flow, ovulation test result, cervical mucus quality and spotting (intermenstrual bleeding), and no time grouping."}),
          el("li", {id: "cycleExport", text: "Or upload the Health app's export (Export All Health Data) under Upload files: its cycle records come in while this is on, and are left out while it's off."})));   // #223
      if (owner) { parts.push(el("div", {class: "row"}, msg)); zone = cycleZone(ME.profiles.find(x => x.id === PID)?.name || "", msg); }
      else parts.push(reach(), el("p", {class: "hint", text: "Only the profile's owners can turn it off or delete what's kept."}));
    }
    if (st.deletion) parts.push(el("p", {class: "hint", id: "cycleDeleted", text: st.deletion.done
      ? `Last deleted ${fmtDT(Date.parse(st.deletion.done))}, from the receiver's database and its archive of uploads (asked ${day(st.deletion.asked)}).`
      : `Deletion asked ${fmtDT(st.deletion.asked * 1000)}: the receiver does it within a minute while it's running, or as soon as it next starts. Until then nothing is shown.`}));
    if (!st.on) parts.push(reach());
    if (zone) parts.push(zone);   // last, apart from the rest (#495)
    box.replaceChildren(...parts);
    box.hidden = false;
    SHOWN = st;
  }
  const onWords = st => `On since ${day(st.since)}. ` + (st.deletion && !st.deletion.done ? "Deleting what was kept: nothing is shown until the receiver has done it."
    : st.count ? `Kept: ${st.count.toLocaleString()} record${st.count === 1 ? "" : "s"}, shown on the Timeline.` : "Nothing received yet.");
  /* an import of the profile saved or undone (import-jobs.js told): what's kept counted again (#534: "Kept: 2 records"
     stayed), the card drawn again only where more than that changed */
  async function again() {
    const p = PID;
    let st;
    try { st = await call(`profiles/${p}/cycle`); } catch (e) { return; }
    if (p !== PID || !SHOWN) return;
    if (st.on && SHOWN.on && st.can_change === SHOWN.can_change && JSON.stringify(st.deletion) === JSON.stringify(SHOWN.deletion)) {
      $("#cycleState").textContent = onWords(st); SHOWN = st;
    } else if (JSON.stringify(st) !== JSON.stringify(SHOWN)) {
      const had = $("#cycle").contains(document.activeElement);
      draw(st);
      if (had) toState();
    }
  }

  (async () => {
    ME = await SgSources.ready;
    if (!ME) return;
    PID = SgSources.pid();
    $("#profile").addEventListener("change", () => { PID = SgSources.pid(); SHOWN = null; load(); });
    document.addEventListener("sg:imports", ev => { if (ev.detail.pid === PID && SHOWN) ev.detail.wait(again()); });
    load();
  })();
})();
