/* Sanguity · ECG recordings and irregular rhythm notifications from a watch (#182): Timeline events made from the watch
   summary's "ecg" list (server/ecg.py), and what the drawer shows of them. What the device said only: its
   classification word for word, its average heart rate when it gave one, the device and how it came; nothing is
   worked out from the trace and no word here says whether it is normal.
   A recording's samples (15,360 for 30 seconds at 512 Hz) aren't in the page's data: they are fetched when its drawer
   opens (GET api/profiles/{pid}/watch/ecg/<hex>) and drawn small, 10 seconds a row (5 on a phone), with a time
   and mV scale.
   Not a table of the samples (#60's table view): 15,360 rows say nothing a table can show. In its place the trace takes
   focus with a text summary for screen readers, ← and → step through it half a second at a time saying the voltage
   there, a small table sums the recording up (duration, sample rate, lead, classification), and "Download as CSV"
   gives every sample, for anyone who wants them in a spreadsheet.
   Plain script, loaded in order by index.html after drawer.js; all files share one global scope. */
"use strict";
/* the trace is drawn at the width it has, so its scale's text is its real size: 10 seconds a row, or 5 on a phone */
/* the watch summary's ECGs as events, ids after nextId; returns the last id used */
function ecgLoad(nextId) {
  for (const x of (WATCH && WATCH.ecg) || []) {
    if (!x || !x.start || !["ecg", "irregular_rhythm"].includes(x.type)) continue;
    const rec = x.type === "ecg";
    const ev = {id: ++nextId, t: x.start, end: x.end || x.start, k: rec ? "ecg" : "rhythm",
      title: rec ? (x.classification ? "ECG: " + x.classification : "ECG") : "Irregular rhythm notification",
      sub: [rec && x.avg_hr != null ? `${x.avg_hr} bpm average` : "", x.device || ""].filter(Boolean).join(" · ")};
    ev.ts = zParse(ev.t); ev.te = zParse(ev.end);
    if (!isFinite(ev.ts)) continue;
    ev._q = (ev.title + " ecg electrocardiogram heart rhythm watch " + (x.device || "")).toLowerCase();
    DATA.events.push(ev); byId.set(ev.id, ev); DATA.details[ev.id] = {...x, type: "ecg"};
  }
  return nextId;
}
const kvList = rows => el("dl", {class: "kv"}, ...rows.filter(x => x[1] != null && x[1] !== "").flatMap(([k, v]) => [el("dt", {text: k}), el("dd", {text: String(v)})]));
/* the drawer's body for either kind */
function ecgBody(e, d) {
  if (d.type !== "ecg") return [];
  if (e.k === "rhythm") return [
    kvList([["Notified", fmtDT(e.ts)], ["Period it covers", e.te > e.ts ? `${fmtDT(e.ts)} – ${fmtDT(e.te)}` : ""], ["Recorded with", d.device], ["Came through", d.via]]),
    el("p", {class: "hint ecg-note", text: "The watch's notification, as it sent it. Sanguity doesn't read heart rhythms; this isn't medical advice."})];
  const holder = el("div", {class: "ecg-rec"});
  const out = [
    kvList([["Classification", d.classification], ["Average heart rate", d.avg_hr != null ? `${d.avg_hr} bpm` : ""], ["Symptoms", d.symptoms],
      ["Recorded with", d.device], ["Software version", d.software], ["Came through", d.via]]),
    el("p", {class: "hint ecg-note", text: "The classification is the device's own, word for word. Sanguity doesn't read ECGs; this isn't medical advice."}),
    el("div", {class: "sect", text: "The recording"}), holder];
  const name = /^ecg:([0-9a-f]{16})$/.exec(d.key || "");
  if (!d.wave || !name) { holder.append(el("p", {class: "hint", text: "The recording's samples didn't come with it, so there's no trace to show."})); return out; }
  const msg = el("p", {class: "hint", role: "status", text: "Loading the recording…"});
  holder.append(msg);
  (async () => {
    try {
      const r = await api(`profiles/${PROFILE.id}/watch/ecg/${name[1]}`);
      if (!r.ok) throw new Error(r.status === 404 ? "its samples aren't here any more" : `${r.status} ${r.statusText}`);
      const w = await r.json();
      if (!holder.isConnected) return;   // the drawer moved on
      holder.replaceChildren(...ecgRecording(e, d, w, holder.clientWidth));
    } catch (err) { if (holder.isConnected) msg.textContent = "Couldn't load the recording: " + err.message + "."; }
  })();
  return out;
}
const ecgNum = (v, n = 2) => (+v.toFixed(n)).toLocaleString(undefined, {maximumFractionDigits: n});
/* the trace, its readout, the summary table and the download, from the samples (µV) */
function ecgRecording(e, d, w, width) {
  const W = Math.round(Math.max(300, Math.min(900, width || 628))), ROW = W < 520 ? 5 : 10;   // px wide; seconds a row
  const S = (w.samples || []).filter(v => typeof v === "number"), rate = +w.rate || +d.rate || 0;
  if (!S.length || !(rate > 0)) return [el("p", {class: "hint", text: "The recording has no samples to draw."})];
  const dur = S.length / rate, rows = Math.ceil(dur / ROW - 1e-9);
  const maxAbs = S.reduce((m, v) => Math.max(m, Math.abs(v)), 0) / 1000;
  const R = Math.min(4, Math.max(1, Math.ceil(maxAbs * 2) / 2));   // ±R mV a row, in half millivolts: the same scale on every row
  const L = 40, PW = W - L - 8, RH = ROW < 10 ? 64 : 84, GAP = 22, H = rows * (RH + GAP) + 4, pxS = PW / ROW;
  const g = [], yOf = (row, mv) => row * (RH + GAP) + 4 + RH / 2 - Math.max(-R, Math.min(R, mv)) / R * (RH / 2);
  for (let row = 0; row < rows; row++) {
    const t0 = row * ROW, secs = Math.min(ROW, dur - t0), top = row * (RH + GAP) + 4;
    for (let s = 0; s <= Math.floor(secs + 1e-9); s++) {   // a line each second, labelled every two (every one, 5 seconds a row)
      const x = L + s * pxS;
      g.push(el("svg:line", {class: "ecg-grid", x1: x, x2: x, y1: top, y2: top + RH}));
      if (s % (ROW / 5) === 0) g.push(el("svg:text", {class: "ecg-tick", x, y: top + RH + 14, "text-anchor": !s ? "start" : s >= secs - 1e-9 ? "end" : "middle", text: `${t0 + s} s`}));
    }
    for (let mv = -R; mv <= R + 1e-9; mv += 0.5) {
      const y = yOf(row, mv), whole = Math.abs(mv % 1) < 1e-9;
      g.push(el("svg:line", {class: mv === 0 ? "ecg-grid ecg-zero" : "ecg-grid", x1: L, x2: L + secs * pxS, y1: y, y2: y, "stroke-opacity": whole ? 1 : .55}));
      if (whole) g.push(el("svg:text", {class: "ecg-tick", x: L - 5, y: y + 3.5, "text-anchor": "end", text: mv ? `${mv > 0 ? "+" : "−"}${Math.abs(mv)} mV` : "0"}));
    }
    // the trace: each column's lowest and highest sample, so no peak is lost at this size
    const pts = [], per = rate / pxS, i0 = Math.round(t0 * rate), i1 = Math.min(S.length, Math.round((t0 + secs) * rate));
    for (let c = 0; i0 + c * per < i1; c++) {
      const a = Math.floor(i0 + c * per), b = Math.min(i1, Math.max(a + 1, Math.floor(i0 + (c + 1) * per)));
      let lo = Infinity, hi = -Infinity;
      for (let i = a; i < b; i++) { if (S[i] < lo) lo = S[i]; if (S[i] > hi) hi = S[i]; }
      const x = (L + c + .5).toFixed(1), yl = yOf(row, lo / 1000).toFixed(1), yh = yOf(row, hi / 1000).toFixed(1);
      pts.push(c % 2 ? `${x},${yh} ${x},${yl}` : `${x},${yl} ${x},${yh}`);
    }
    g.push(el("svg:polyline", {class: "ecg-line", points: pts.join(" ")}));
  }
  const cursor = el("svg:line", {class: "ecg-cursor", y1: 0, y2: 0, x1: 0, x2: 0, visibility: "hidden"});
  g.push(cursor);
  const svg = el("svg:svg", {class: "ecg-svg", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "xMinYMin meet"}, ...g);
  const readout = el("p", {class: "ecg-readout", "aria-hidden": "true", text: " "});
  const lead = w.lead || d.lead, cls = d.classification;
  const at = t => S[Math.min(S.length - 1, Math.max(0, Math.round(t * rate)))] / 1000;
  const show = t => {
    const row = Math.min(rows - 1, Math.floor(t / ROW + 1e-9)), x = L + (t - row * ROW) * pxS, top = row * (RH + GAP) + 4;
    cursor.setAttribute("x1", x); cursor.setAttribute("x2", x); cursor.setAttribute("y1", top); cursor.setAttribute("y2", top + RH);
    cursor.setAttribute("visibility", "visible");
    const words = `At ${ecgNum(t, 1)} s: ${ecgNum(at(t))} mV`;
    readout.textContent = words;
    return words;
  };
  const clear = () => { cursor.setAttribute("visibility", "hidden"); readout.textContent = " "; };
  const steps = []; for (let t = 0; t <= dur + 1e-9; t += 0.5) steps.push(+t.toFixed(1));
  chartKeys(svg, `ECG recording, ${ecgNum(dur, 1)} seconds at ${ecgNum(rate)} Hz${lead ? ", " + lead : ""}${cls ? `, classified by the device as ${cls}` : ""}. `
    + "The left and right arrow keys step through it half a second at a time, saying the voltage there.", () => steps, show, clear);
  svg.addEventListener("pointermove", ev => {
    const r = svg.getBoundingClientRect(), k = W / r.width, x = (ev.clientX - r.left) * k, y = (ev.clientY - r.top) * k;
    const row = Math.min(rows - 1, Math.max(0, Math.floor(y / (RH + GAP)))), t = row * ROW + (x - L) / pxS;
    if (x < L || t < 0 || t > dur) { clear(); return; }
    show(t);
  });
  svg.addEventListener("pointerleave", () => { if (svg._kt == null) clear(); });
  const table = el("table", {class: "data ecg-sum"}, el("caption", {class: "vh", text: "The recording, summed up"}),
    el("tbody", {}, [["Duration", `${ecgNum(dur, 1)} s`], ["Sample rate", `${ecgNum(rate)} Hz`], ["Lead", lead], ["Classification", cls],
      ["Samples", S.length.toLocaleString()], ["Unit", "µV as recorded; drawn in mV"]].filter(x => x[1])
      .map(([k, v]) => el("tr", {}, el("th", {scope: "row", text: k}), el("td", {text: v})))));
  const say = el("p", {class: "hint", role: "status"});
  const dl = el("button", {class: "btn", type: "button", text: "Download as CSV", onclick: () => {
    const lines = ["Time (s),Voltage (µV)", ...S.map((v, i) => `${+(i / rate).toFixed(6)},${v}`)];
    const blob = new Blob([lines.join("\r\n") + "\r\n"], {type: "text/csv;charset=utf-8"});
    const z = zdate(e.ts), two = n => String(n).padStart(2, "0"), name = `ecg-${isoDay(e.ts)}-${two(z.getHours())}${two(z.getMinutes())}.csv`;
    const url = URL.createObjectURL(blob), a = el("a", {href: url, download: name, hidden: true});
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    sayIn(say, `Downloaded ${name}: ${S.length.toLocaleString()} samples.`);
  }});
  return [el("figure", {class: "ecg-trace"}, svg, readout), table, el("div", {class: "btns"}, dl), say];
}
