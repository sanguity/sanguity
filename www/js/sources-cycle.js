/* Sanguity · Sources → Phone and watch → Cycle tracking (#183; server/cycle.py, cycle_tracking.py, routes_cycle.py).
   Off for every profile until one of its owners turns it on here. Its owners and editors see this card (editors read
   it: only owners turn it on or off, or delete what's kept); viewers don't see it at all, and aren't told whether it's
   on. The card says plainly what's kept, who sees it, where it never goes, and what deleting reaches and what it
   can't. The Sources page signs in and chooses the profile (sources-page.js). */
"use strict";   // $, el, api, ask, sayIn, moreAbout come from util.js
(() => {
  async function call(path, method = "GET", body) {
    const r = await api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});
    if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((typeof j.detail === "string" && j.detail) || r.status + " " + r.statusText);
    return j;
  }
  let ME = null, PID = null;
  const day = t => fmtD(t * 1000);
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
  /* what deleting reaches, and what it can't (said before turning it on, and while it's on) */
  const reach = () => el("div", {class: "cy-reach"},
    el("h3", {class: "sub", text: "What deleting reaches"}),
    el("ul", {class: "cy-list"},
      el("li", {text: "Deleted: what the watch receiver keeps (its database, every upload it archived, rewritten without them, and its summary for this page). Sanguity's own database never holds the records."}),
      el("li", {text: "Not reached: backups of the server or of the receiver's folder made before you delete. A backup can't be rewritten: it keeps what it had until it's deleted itself."}),
      el("li", {text: "Not reached: exports and visit summaries already downloaded with them ticked in, and an Apple Health export you uploaded (it's kept as uploaded and may hold cycle records Sanguity doesn't read; undo that import under Import history to delete it)."}),
      el("li", {text: "Not reached: your phone. Health Connect or Apple Health, and the app that sends them, keep their own."})));

  function draw(st) {
    const box = $("#cycle"), msg = el("span", {class: "msg", id: "cycleMsg", role: "status"}), owner = st.can_change;
    const parts = [el("h2", {text: "Cycle tracking"})];
    const send = async (path, method, body, btn) => {
      btn.disabled = true;
      try { draw(await call(path, method, body)); sayIn($("#cycleMsg"), method === "PUT" ? (body.on ? "Cycle tracking is on." : "Cycle tracking is off. What was kept is being deleted.") : "Deleting what was kept."); }
      catch (e) { sayIn(msg, e.message, true); btn.disabled = false; }
    };
    if (!st.on) {
      parts.push(el("p", {class: "hint", id: "cycleState", text: `Off. ${KEPT} from the phone apps are left out of every upload before anything is kept.`}),
        moreAbout(`Turned on, ${KEPT.toLowerCase()} from Health Connect (Life Dashboard Companion) or Apple Health (Health Auto Export) are kept for this profile. ${WHO}`, NEVER, SEX, WORDS));
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
      const d = st.deletion, waiting = d && !d.done;
      parts.push(el("p", {class: "hint", id: "cycleState", text: `On since ${day(st.since)}. ` + (waiting ? "Deleting what was kept: nothing is shown until the receiver has done it."
        : st.count ? `Kept: ${st.count} record${st.count === 1 ? "" : "s"}, shown on the Timeline.` : "Nothing received yet.")}),
        moreAbout(WHO, NEVER, SEX, WORDS),
        el("h3", {class: "sub", text: "Sending them from the phone"}),
        el("ul", {class: "cy-list"},
          el("li", {text: "Android: in Health Connect, let Life Dashboard Companion read cycle tracking (menstruation, ovulation tests, cervical mucus, spotting). It sends them with the rest."}),
          el("li", {text: "iPhone: in Health Auto Export, add a REST API automation like the others, with Cycle Tracking's menstrual flow, ovulation test result, cervical mucus quality and spotting (intermenstrual bleeding), and no time grouping."})),
        reach());
      if (owner) {
        const del = el("button", {class: "btn", type: "button", id: "cycleDelete", text: "Delete all cycle data…", onclick: async () => {
          if (!await ask({title: "Delete all cycle data?", action: "Delete all", danger: true, message: [
            "Everything kept for this profile is deleted: the receiver's database and every upload it archived. Cycle tracking stays on, so new records from the phone are kept again.",
            "Backups made before now, downloaded exports and summaries, uploaded Apple Health exports and your phone keep their own copies: deleting can't reach them."]})) return;
          send(`profiles/${PID}/cycle/delete`, "POST", {}, del);
        }});
        const off = el("button", {class: "btn", type: "button", id: "cycleOff", text: "Turn off…", onclick: async () => {
          if (!await ask({title: "Turn off cycle tracking?", action: "Turn off and delete", danger: true, message: [
            "Turning it off deletes everything kept for this profile, and from then on cycle records are left out of every upload again.",
            "Backups made before now, downloaded exports and summaries, uploaded Apple Health exports and your phone keep their own copies: deleting can't reach them."]})) return;
          send(`profiles/${PID}/cycle`, "PUT", {on: false}, off);
        }});
        parts.push(el("div", {class: "row"}, del, off, msg));
      } else parts.push(el("p", {class: "hint", text: "Only the profile's owners can turn it off or delete what's kept."}));
    }
    if (st.deletion) parts.push(el("p", {class: "hint", id: "cycleDeleted", text: st.deletion.done
      ? `Last deleted ${fmtDT(Date.parse(st.deletion.done))}, from the receiver's database and its archive of uploads (asked ${day(st.deletion.asked)}).`
      : `Deletion asked ${fmtDT(st.deletion.asked * 1000)}: the receiver does it within a minute while it's running, or as soon as it next starts. Until then nothing is shown.`}));
    if (!st.on) parts.push(reach());
    box.replaceChildren(...parts);
    box.hidden = false;
  }

  (async () => {
    ME = await SgSources.ready;
    if (!ME) return;
    PID = SgSources.pid();
    $("#profile").addEventListener("change", () => { PID = SgSources.pid(); load(); });
    load();
  })();
})();
