/* Sanguity · tabs, render dispatch and startup (signs in, picks the profile, loads its data, settings and watch data).
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- tabs & boot */
const TABS = ["overview", "health", "timeline", "labs", "heatmap", "costs"];
function switchTab(t) {
  if (!TABS.includes(t)) t = "overview";
  const changed = t !== state.tab;
  if (changed && drawer.classList.contains("open")) closeDrawer();   // details belong to the tab they were opened from
  hideTip(); state.tab = t;
  { const b = $("#tab-" + t), bar = b.parentElement; if (bar.scrollWidth > bar.clientWidth) bar.scrollTo({left: bar.scrollLeft + b.getBoundingClientRect().left - bar.getBoundingClientRect().left - (bar.clientWidth - b.offsetWidth) / 2, behavior: "smooth"}); }
  for (const k of TABS) { $("#tab-" + k).setAttribute("aria-selected", t === k); $("#view-" + k).hidden = t !== k; }
  $(".filters").style.display = t === "overview" || t === "costs" || t === "health" ? "none" : "";
  $("#q").placeholder = t === "timeline" ? "Search events…" : "Filter lab names…"; $("#chips").style.display = t === "timeline" ? "" : "none";
  renderAll();
  if (changed) window.scrollTo({top: 0, left: 0, behavior: "instant"});   // each tab starts at the top
}
for (const k of TABS) $("#tab-" + k).onclick = () => switchTab(k);
$("#optMarks").onchange = e => { state.marks = e.target.checked; renderCharts(); save(); };
$("#optSmooth").onchange = e => { state.smooth = e.target.checked; renderCharts(); save(); };
function renderAll() {
  renderFilters();
  $("#optMarks").checked = state.marks; $("#optSmooth").checked = state.smooth;
  if (state.tab === "timeline") { renderStats(); renderTimeline(); renderList(); }
  else if (state.tab === "labs") renderLabs();
  else if (state.tab === "heatmap") renderHeatmap();
  else if (state.tab === "costs") renderCosts();
  else if (state.tab === "health") renderHealth();
  else renderOverview();
  save();
}
/* no records to show (an empty profile, none shared, or an error): a message instead of the tabs, and none of the
   controls that would render them, since those need DATA and the tab sections this replaces */
function emptyCard(...kids) {
  $(".tabs").hidden = true; $(".filters").hidden = true;
  document.querySelector("main").replaceChildren(el("div", {class: "card empty"}, ...kids));
}
function renderProfileMenu() {
  const sel = $("#profile"), list = ME.profiles;
  sel.replaceChildren(...list.map(p => el("option", {value: p.id, text: p.name + (p.role === "viewer" ? " (view only)" : "")})));
  sel.value = PROFILE.id; sel.hidden = list.length < 2;
  sel.onchange = () => { try { localStorage.setItem("ht-profile", sel.value); } catch (e) {} location.reload(); };
}
$("#logout").onclick = async () => { try { await api("logout", {method: "POST"}); } catch (e) {} location.replace("login.html"); };
(async function boot() {
  try {
    const m = await api("me");
    if (m.status === 401) { const s = await api("setup").then(r => r.json()).catch(() => ({})); location.replace(s.needed ? "setup.html" : "login.html"); return; }
    if (!m.ok) throw new Error(m.status + " " + m.statusText);
    ME = await m.json();
    if (ME.user.must_change_pw) { location.replace("account.html#password"); return; }
    if (ME.user.needs_two_factor) { location.replace("account.html#twofa"); return; }
    $("#who").textContent = ME.user.username;
    if (!ME.profiles.length) { emptyCard("No health profiles are shared with you yet. ", el("a", {href: "account.html#profiles", text: "Create one"}), " or ask a profile's owner to share it with ", el("b", {text: ME.user.username}), "."); return; }
    let want; try { want = +localStorage.getItem("ht-profile"); } catch (e) {}
    PROFILE = ME.profiles.find(p => p.id === want) || ME.profiles[0];
    hLoad();
    renderProfileMenu();
    const r = await api(`profiles/${PROFILE.id}/data`);
    if (r.status === 401) { location.replace("login.html"); return; }
    if (r.status >= 500) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || r.status + " " + r.statusText); }
    if (r.status === 204) { emptyCard(`${PROFILE.name} has no records yet. Importing records in the app is coming in a later release; for now an admin can load a built data.json with `, el("code", {text: `python -m server.manage import ${PROFILE.id} DIR`}), "."); return; }
    if (!r.ok) throw new Error(r.status + " " + r.statusText);
    DATA = await r.json();
    // milestones (older data.json files carry a single dxDate instead)
    MILESTONES = (DATA.milestones || (DATA.dxDate ? [{date: DATA.dxDate, label: "Diagnosis"}] : [])).filter(m => m && /^\d{4}-\d{2}-\d{2}/.test(m.date || ""))
      .map(m => ({...m, date: m.date.slice(0, 10), label: m.label || "Milestone"})).sort((a, b) => a.date.localeCompare(b.date));
    // the saved "dx" period, or a milestone that no longer exists, falls back to the primary milestone or All
    if (isMsKey(costState.period) && !msOf(costState.period) || costState.period === "dx") costState.period = msKey(primaryMs());
    if (isMsKey(hState.period) && !msOf(hState.period) || hState.period === "dx") hState.period = primaryMs() ? msKey(primaryMs()) : "all";
  } catch (e) { emptyCard("Couldn't load the records: " + e.message); return; }
  // this profile's preferences; missing or broken settings keep the defaults
  try {
    const s = await api(`profiles/${PROFILE.id}/settings`);
    if (s.ok) applySettings(await s.json());
  } catch (e) { console.warn("settings ignored:", e.message); }
  for (const e of DATA.events) {
    e.ts = Date.parse(e.t); e.te = e.end ? Date.parse(e.end) : e.ts;
    e._q = (e.title + " " + (e.sub || "") + " " + (KIND_LABEL[e.k] || "")).toLowerCase();
    byId.set(e.id, e);
    if (e.k === "lab") { const d = DATA.details[e.id]; e.n = d ? d.ids.length : 1; }
  }
  EV = DATA.events.filter(e => e.k !== "stay" ? true : true);
  STAYS = DATA.events.filter(e => e.k === "stay");
  // let search hit lab-day events through their child results' names
  for (const e of DATA.events) if (e.k === "lab") { const d = DATA.details[e.id]; if (d) e._q += " " + d.ids.map(i => (byId.get(i)?.title || "")).join(" ").toLowerCase(); }
  SERIES = DATA.labs; for (const s of SERIES) for (const p of s.pts) p.ts = Date.parse(p[0]);
  // optional: watch data from the Health Connect receiver
  try {
    const w = await api(`profiles/${PROFILE.id}/watch`);
    if (w.status === 200) WATCH = await w.json();
  } catch (e) {}
  if (WATCH) {
    const M = Object.fromEntries((WATCH.metrics || []).map(m => [m.key, m]));
    const byDay = k => Object.fromEntries(M[k] ? M[k].pts : []);
    const mkPts = m => m.pts.map(([d, v]) => { const p = [d + "T12:00:00", v, null, null, "", ""]; p.ts = Date.parse(p[0]); p.day = d; return p; });
    const add = (m, extra) => { const pts = mkPts(m); const s = {name: m.name, unit: m.unit, panel: m.group === "Body" ? "Body · smart scale" : "Watch · " + m.group, n: pts.length, pts, txt: [], watch: true, key: m.key, kind: "line", ...extra}; SERIES.push(s); return s; };
    if (M.hr_avg) {   // one chart: daily average inside a low–high band
      const lo = byDay("hr_min"), hi = byDay("hr_max");
      const s = add(M.hr_avg, {name: "Heart rate", key: "hr", kind: "band"});
      for (const p of s.pts) { p.lo = lo[p.day]; p.hi = hi[p.day]; }
    }
    if (M.sleep_h) {  // stacked deep / REM / light per night
      const parts = {deep: byDay("sleep_deep_min"), rem: byDay("sleep_rem_min"), light: byDay("sleep_light_min")};
      const s = add(M.sleep_h, {name: "Sleep", key: "sleep", kind: "stack"});
      for (const p of s.pts) p.parts = {deep: (parts.deep[p.day] || 0) / 60, rem: (parts.rem[p.day] || 0) / 60, light: (parts.light[p.day] || 0) / 60};
    }
    if (M.steps) { add(M.steps, {kind: "bars"}); STEPS = mkPts(M.steps).map(p => [Date.parse(p.day + "T00:00:00"), p[1]]); }
    const cw = DATA.clinicWeights || [];
    if (M.weight || cw.length) {
      const s = add(M.weight || {key: "weight", name: "Weight", unit: "lb", group: "Body", pts: []}, {name: "Body weight", key: "weight"});
      const have = new Set(s.pts.map(p => p.day));
      for (const [t, v, org] of cw) {
        const day = t.slice(0, 10); if (have.has(day)) continue; have.add(day);
        const p = [day + "T12:00:00", v, null, null, "", "", "Clinic"]; p.ts = Date.parse(p[0]); p.day = day; p.org = org; s.pts.push(p);
      }
      s.pts.sort((a, b) => a.ts - b.ts); s.n = s.pts.length;
    }
    const folded = new Set(["weight", "hr_avg", "hr_min", "hr_max", "sleep_h", "sleep_deep_min", "sleep_rem_min", "sleep_light_min", "sleep_awake_min", "steps"]);
    for (const m of WATCH.metrics || []) if (!folded.has(m.key)) add(m);
    let wid = DATA.events.reduce((m, e) => Math.max(m, +e.id || 0), 1e7);   // after every record's id
    for (const x of WATCH.exercise || []) {
      const ev = {id: ++wid, t: x.start, end: x.end, k: "exercise", title: x.name.replace(/_/g, " "), sub: `${x.minutes} min`};
      ev.ts = Date.parse(ev.t); ev.te = Date.parse(ev.end); ev._q = (ev.title + " workout exercise").toLowerCase();
      DATA.events.push(ev); byId.set(ev.id, ev); DATA.details[ev.id] = {type: "exercise", kind: x.kind, minutes: x.minutes};
    }
    EV = DATA.events;
  }
  const ts = DATA.events.map(e => e.ts).filter(isFinite); state.min = Math.min(...ts); state.max = Math.max(...ts);
  for (const s of SERIES) if (s.watch && s.pts.length) state.max = Math.max(state.max, s.pts[s.pts.length - 1].ts);
  $("#meta").textContent = `${DATA.source} · built ${fmtD(DATA.built)}` + (WATCH && WATCH.last_payload ? ` · watch synced ${fmtDT(WATCH.last_payload)}` : "");
  restore();
  if (!state.sel.size) state.sel = new Set(CFG.trends.defaultLabs.filter(n => SERIES.some(s => s.name === n)));
  if (state.preset !== "custom") applyPreset(state.preset); else renderAll();
  switchTab(state.tab);
})();
