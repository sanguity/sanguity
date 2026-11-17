/* Sanguity · Labs tab, as a heatmap (#172; the charts are trends.js): every lab with ranges, a column per draw.
   Plain script, loaded in order by index.html; all files share one global scope. */
"use strict";
/* ---------------------------------------------------------------- heatmap */
function hmLevel(v, lo, hi) {
  if (lo == null && hi == null) return null;
  const span = lo != null && hi != null && hi > lo ? hi - lo : Math.abs(hi ?? lo) || 1;
  let z = 0;
  if (lo != null && v < lo) z = -(lo - v) / span; else if (hi != null && v > hi) z = (v - hi) / span;
  if (z === 0) return 0;
  const a = Math.abs(z), step = a <= .1 ? 1 : a <= .3 ? 2 : a <= .6 ? 3 : 4;
  return Math.sign(z) * step;
}
const hmFill = lv => lv === 0 ? "var(--div-mid)" : `color-mix(in oklab, var(--div-mid), var(${lv < 0 ? "--div-lo" : "--div-hi"}) ${Math.abs(lv) * 25}%)`;
function renderHeatmap() {
  const asTable = state.tables.has("#hm");   // Heatmap | Table (#60)
  const hd = $("#hmHd"); hd.querySelector(".view-seg")?.remove(); hd.append(viewSeg("#hm", "the heatmap", renderHeatmap, "Heatmap"));
  $("#hmLegend").hidden = $("#hmWrap").hidden = asTable; $("#hmTable").hidden = !asTable;
  const all = SERIES.filter(s => !s.watch && s.n >= 20 && s.pts.some(p => p[2] != null || p[3] != null)), rows = all.filter(labMatch);   // the Labs filter (#192, trends.js)
  labCount("#hmQn", rows.length, all.length);
  const panelOrder = ["CBC WITH DIFFERENTIAL", "COMPREHENSIVE METABOLIC PANEL"];
  const prank = p => { const i = panelOrder.indexOf(p); return i < 0 ? 9 : i; };
  rows.sort((a, b) => prank(a.panel) - prank(b.panel) || a.panel.localeCompare(b.panel) || b.n - a.n);
  // columns: draw days in range
  const dayKey = ts => isoDay(ts);
  const days = new Map();
  for (const s of rows) for (const p of s.pts) if (p.ts >= state.from && p.ts <= state.to) { const k = dayKey(p.ts); if (!days.has(k)) days.set(k, dayTs(k)); }
  const cols = [...days.entries()].sort((a, b) => a[1] - b[1]);
  if (asTable) { hmTable(rows, cols); return; }
  const svg = $("#hm"), axis = $("#hmAxis");
  const avail = Math.max(320, svg.parentElement.clientWidth);
  const LW = avail < 600 ? 120 : 190, top = 34, rowH = 14, gap = 2;
  if (!rows.length || !cols.length) {   // keep it draggable so you can pan back to where the draws are
    svg.replaceChildren(el("svg:text", {x: LW, y: 20, text: !rows.length && state.labQ ? "No lab names match the filter." : "No lab draws in this date range."})); svg.setAttribute("viewBox", `0 0 ${avail} 30`); svg.setAttribute("width", avail); svg.setAttribute("height", 30);
    svg._geom = {L: LW, R: avail - 8, w: avail}; axis.replaceChildren(); axis.setAttribute("height", 0); return;
  }
  // columns always fit the width (fractional widths are fine); zoom in to get wider cells
  const cw = Math.max(1, Math.min(22, (avail - LW - 8) / cols.length));
  // the colours, and the marks that say the same without them (#60): ▲ above the range, ▼ below, on cells wide enough
  $("#hmLegend").replaceChildren(...[-4, -3, -2, -1, 0, 1, 2, 3, 4].map(lv => el("i", {style: {background: hmFill(lv)}, title: lv})),
    el("span", {text: "← far below · below · in range · above · far above →"}), el("span", {style: {color: "var(--muted)"}, text: "(distance outside the range, as a share of the range's width)"}),
    el("span", {class: "hm-key"}, el("b", {"aria-hidden": "true", text: "▲"}), " above the range, ", el("b", {"aria-hidden": "true", text: "▼"}), " below",
      cw < 3 ? el("span", {style: {color: "var(--muted)"}, text: " (zoom in to see them on each cell, or show the table)"}) : null));
  const groups = []; let gy = top;
  const yOf = new Map();
  let lastPanel = null;
  for (const s of rows) { const p = s.panel || "Other"; if (p !== lastPanel) { groups.push([p, gy]); gy += 16; lastPanel = p; } yOf.set(s.name, gy); gy += rowH; }
  const W = LW + cols.length * cw + 8, H = gy + 4, AH = 22;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("width", W); svg.setAttribute("height", H);
  svg._geom = {L: LW, R: LW + cols.length * cw, w: W};
  axis.setAttribute("viewBox", `0 0 ${W} ${AH}`); axis.setAttribute("width", W); axis.setAttribute("height", AH);
  const ax = [];
  const g = [];
  const colX = new Map(cols.map(([k], i) => [k, LW + i * cw]));
  // hospital stay strip + procedure markers
  for (const [k, ts] of cols) {
    const cx = colX.get(k);
    if (STAYS.some(st => ts >= st.ts - DAY / 2 && ts <= st.te + DAY / 2)) g.push(el("svg:rect", {x: cx, y: 8, width: cw, height: 6, fill: "var(--muted)", "fill-opacity": .5}));
    const procs = EV.filter(e => e.k === "procedure" && isoDay(e.ts) === k);
    if (procs.length) { const c = el("svg:text", {x: cx + cw / 2, y: 28, "text-anchor": "middle", style: "fill:var(--c-procedure);font-size:10px", text: "◆"}); c.dataset.proc = procs.map(e => e.title).join("; "); c.dataset.day = ts; g.push(c); }
  }
  g.push(el("svg:text", {x: LW - 8, y: 14, "text-anchor": "end", text: "Hospital"}));
  g.push(el("svg:text", {x: LW - 8, y: 28, "text-anchor": "end", text: "Procedure"}));
  for (const [p, py] of groups) g.push(el("svg:text", {class: "grp", x: 0, y: py + 11, text: p.length > 30 ? p.slice(0, 29) + "…" : p}));
  for (const s of rows) {
    const ry = yOf.get(s.name);
    const nm = nice(s.name), edit = canRename();   // an editor renames a row by its name (#38, rename.js)
    g.push(el("svg:text", {class: "row" + (edit ? " renamable" : ""), x: LW - 8, y: ry + rowH - 4, "text-anchor": "end",
      ...(edit ? {"data-rename": "lab", "data-key": s.name, tabindex: 0, role: "button", "aria-label": `Rename ${nm}`} : {})},
      nm.length > (LW < 150 ? 17 : 28) ? nm.slice(0, LW < 150 ? 16 : 27) + "…" : nm,
      el("svg:title", {text: renamedTitle("lab", s.name) + (edit ? " · click to rename" : "")})));
    const byDay = new Map(); for (const p of s.pts) if (p.ts >= state.from && p.ts <= state.to) byDay.set(dayKey(p.ts), p);
    for (const [k, p] of byDay) {
      const lv = hmLevel(p[1], p[2], p[3]);
      const r = el("svg:rect", {class: "cell", x: colX.get(k) + (cw > 6 ? .5 : 0), y: ry + 1, width: cw - (cw > 6 ? 1 : 0), height: rowH - gap, rx: cw > 8 ? 2 : 0, style: `fill:${lv == null ? "var(--grid)" : hmFill(lv)}`});
      r.dataset.s = s.name; r.dataset.k = k;
      g.push(r);
      if (lv && cw >= 3) g.push(abnMark(colX.get(k) + cw / 2, ry + 1 + (rowH - gap) / 2, lv > 0 ? "H" : "L", Math.min(3.6, cw / 2 - .8), "hm-mark"));   // #60
    }
  }
  // month labels go in a separate axis strip that sticks to the bottom of the window until the heatmap's end scrolls into view
  let lastM = "", lastLabelX = -1e9;
  for (const [k, ts] of cols) {
    const m = k.slice(0, 7); if (m === lastM) continue; lastM = m;
    const cx = colX.get(k);
    g.push(el("svg:line", {x1: cx, x2: cx, y1: top - 2, y2: H, stroke: "var(--grid)"}));
    ax.push(el("svg:line", {x1: cx, x2: cx, y1: 0, y2: 5, stroke: "var(--axis)"}));
    if (cx - lastLabelX >= 44) { ax.push(el("svg:text", {x: cx + 2, y: AH - 6, text: zdate(ts).toLocaleDateString(undefined, {month: "short", year: "2-digit"})})); lastLabelX = cx; }
  }
  ax.push(el("svg:text", {x: LW - 8, y: AH - 6, "text-anchor": "end", text: "Draw date"}));
  svg.replaceChildren(...g); axis.replaceChildren(...ax);
  svg.onpointermove = ev => {
    const d = ev.target.dataset || {};
    if (d.proc) { showTip([el("div", {class: "v", text: d.proc}), el("div", {class: "s", text: fmtD(+d.day)})], ev); return; }
    if (!d.s) { hideTip(); return; }
    const s = seriesBy(d.s), p = s.pts.filter(p => isoDay(p.ts) === d.k).pop(); if (!p) return;
    const st = statusOf(p);
    showTip([el("div", {class: "v", text: `${p[5] || fmtNum(p[1])} ${s.unit}`}), el("div", {class: "s", text: nice(s.name) + (nice(s.name) !== s.name ? ` (${s.name})` : "")}), el("div", {class: "s", text: `${fmtDT(p.ts)} · ${refText(p)}`}),
      ucOrig(s, p) ? el("div", {class: "s", text: "Reported as " + ucOrig(s, p)}) : null,   // #41
      st ? el("div", {class: "s", style: {color: st.cls === "ok" ? "var(--good-text)" : "var(--crit)"}, text: st.text}) : null,
      pointSources(s, p) ? el("div", {class: "s", text: "From " + pointSources(s, p)}) : null], ev);   // with any linked as the same (#96)
  };
  svg.onpointerleave = hideTip;
  svg.onkeydown = ev => { const d = ev.target.dataset || {}; if (d.rename && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); openRename(ev.target, "lab", d.key); } };
  svg.onclick = ev => {
    const d = ev.target.dataset || {};
    if (d.rename) { openRename(ev.target, "lab", d.key); return; } const k = d.k || (d.day && isoDay(+d.day)); if (!k) return;
    const e = EV.find(e => e.k === "lab" && isoDay(e.ts) === k) || EV.find(e => e.k === "procedure" && isoDay(e.ts) === k); if (e) openEvent(e);
  };
}
/* the heatmap as a table (#60): a row per lab, grouped by panel as on the heatmap, a column per draw day; a result
   outside its reference range has ▲ or ▼ beside it, said as "high" or "low" */
function hmTable(rows, cols) {
  const box = $("#hmTable");
  if (!rows.length || !cols.length) { box.replaceChildren(el("p", {class: "hint", text: "No lab draws in this date range."})); return; }
  const bodies = [];
  let panel = null, tb = null;
  for (const s of rows) {
    if ((s.panel || "Other") !== panel) {
      panel = s.panel || "Other"; tb = el("tbody", {}); bodies.push(tb);
      tb.append(el("tr", {class: "grp"}, el("th", {scope: "rowgroup", colspan: cols.length + 1, text: panel})));
    }
    const byDay = new Map(); for (const p of s.pts) if (p.ts >= state.from && p.ts <= state.to) byDay.set(isoDay(p.ts), p);
    tb.append(el("tr", {}, el("th", {scope: "row", title: renamedTitle("lab", s.name), text: `${nice(s.name)}${s.unit ? ` (${s.unit})` : ""}`}),
      ...cols.map(([k]) => {
        const p = byDay.get(k); if (!p) return el("td", {});
        const lv = hmLevel(p[1], p[2], p[3]), w = lv ? FLAG_OF(lv > 0 ? "H" : "L") : null;   // as the heatmap colours it
        return el("td", {class: w ? "abn" : null, title: p[2] != null || p[3] != null ? refText(p) : null}, p[5] || fmtNum(p[1]),
          w ? el("span", {"aria-hidden": "true", text: " " + w[0]}) : null, w ? el("span", {class: "vh", text: " " + w[1].toLowerCase()}) : null);
      })));
  }
  box.replaceChildren(el("p", {class: "hint", text: `${rows.length} lab${rows.length === 1 ? "" : "s"} on ${cols.length} draw day${cols.length === 1 ? "" : "s"}. ▲ above its reference range, ▼ below.`}),
    el("div", {class: "tablewrap hm-table"}, el("table", {class: "data"},
      el("thead", {}, el("tr", {}, el("th", {scope: "col", text: "Lab"}), ...cols.map(([, ts]) => el("th", {scope: "col", text: fmtD(ts)})))), ...bodies)));
}

