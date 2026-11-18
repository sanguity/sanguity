/* Sanguity · import page, "Type them in" tab (#27): lab results typed in by hand, one row each, and the other kinds of
   record typed into forms of their own (import-records.js), saved together as one import.
   Rows can be added, removed and pasted from a spreadsheet (several cells at once fill the rows from the one pasted
   into, by the copied header if it has one, else column by column). Each change is checked by the server, which reads
   the rows exactly as the CSV importer reads a spreadsheet's (POST profiles/{id}/manual, save false), and the row
   says what it became: the test it's charted as, the value, the range and the flag. Save stores them (save true).
   Test names are suggested from lab-aliases.json's canonical names (GET lab-names). Rows and records the profile
   has already are pointed out, and left out of the save unless "Save again what's already there" is ticked.
   Plain script after util.js and import.js; its own names stay inside this function. */
"use strict";
(function () {
  const FIELDS = [   // [field, caption, placeholder]: nothing that could pass for a value typed in
    ["date", "Date", "YYYY-MM-DD"], ["time", "Time", "optional"], ["test", "Test"], ["value", "Value"],
    ["unit", "Unit"], ["range", "Range", "low - high"], ["flag", "Flag"], ["lab", "Lab", "optional"]];
  const MAX = 200;   // rows the server takes at once
  // headers a pasted spreadsheet may have, as csv_labs.FIELDS spells them (the common ones)
  const HEADERS = {date: /^(date|collected|collection date|draw date|test date|result date|specimen date|date collected)$/,
    time: /^(time|collection time|draw time)$/, test: /^(test|test name|analyte|component|name|lab test|parameter|marker)$/,
    value: /^(value|result|result value|results|reading|your value|level)$/, unit: /^(unit|units|uom|unit of measure)$/,
    range: /^(range|reference range|ref range|reference interval|normal range|reference)$/,
    low: /^(low|ref low|range low|lower limit|min|minimum|normal low)$/, high: /^(high|ref high|range high|upper limit|max|maximum|normal high)$/,
    flag: /^(flag|flags|abnormal flag|abnormal|interpretation|h l|hl)$/, lab: /^(lab|laboratory|performing lab|facility)$/};
  const box = $("#manRows"), msg = $("#manMsg"), saveBtn = $("#manSave"), recs = window.sanguityRecords;
  const keepBox = $("#manKeep"), BODY_MAX = 60000;   // nginx refuses API bodies over 64 KB
  let timer = null, seq = 0, ok = 0, bad = 0, okRecs = 0, badRecs = 0, saving = false;

  const pid = () => +$("#profile").value;
  const tell = (text, isBad = false, link = null, urgent = isBad) => {   // urgent: said at once (sayIn, util.js)
    sayIn(msg, [text, ...(link ? [" ", link] : [])], urgent, {repeat: urgent}); msg.classList.toggle("bad", isBad);
  };
  const today = zToday;   // dates.js
  const rows = () => [...box.querySelectorAll(".man-row")];
  const valueOf = r => Object.fromEntries(FIELDS.map(([f]) => [f, r.querySelector(`[data-f=${f}]`).value.trim()]));
  // a row with only what a new row is given (date, time, lab) is still empty
  const blank = v => !["test", "value", "unit", "range", "flag"].some(f => v[f]);

  function field([f, cap, ph]) {
    const input = f === "flag"
      ? el("select", {"data-f": f}, el("option", {value: "", text: "–"}), el("option", {value: "H", text: "High"}), el("option", {value: "L", text: "Low"}))
      : el("input", {"data-f": f, type: "text", placeholder: ph, autocomplete: "off", spellcheck: "false",
                     inputmode: f === "value" ? "decimal" : null, list: f === "test" ? "labNames" : null,
                     maxlength: {date: 40, time: 20, test: 120, value: 60, unit: 30, range: 60, lab: 120}[f]});
    return el("label", {class: "f-" + f}, el("span", {class: "cap", text: cap}), input);
  }
  function addRow(v = {}, after = null) {
    if (rows().length >= MAX) { tell(`At most ${MAX} rows at a time: save these first.`, true); return null; }
    const last = after || rows().pop(), from = last ? valueOf(last) : {};
    const r = el("div", {class: "man-row", role: "group"}, FIELDS.map(field),
      el("button", {type: "button", class: "del", title: "Remove this row", "aria-label": "Remove this row"}, icon("x")),
      el("div", {class: "man-check", "aria-live": "polite"}));
    // a new row starts on the last one's date, time and lab: a report's results share them
    for (const [f] of FIELDS) r.querySelector(`[data-f=${f}]`).value = v[f] ?? (["date", "time", "lab"].includes(f) ? from[f] || (f === "date" ? today() : "") : "");
    if (after) after.after(r); else box.append(r);
    label();
    return r;
  }
  function label() {   // each row's group is named by its number, so "Remove this row" and the checks say which
    rows().forEach((r, i) => r.setAttribute("aria-label", `Result ${i + 1}`));
  }

  /* ---------------------------------------------------------------- checking as you go */
  function soon() { clearTimeout(timer); timer = setTimeout(check, 350); }
  const body = (save, rs) => JSON.stringify({rows: rs.map(valueOf).map(v => blank(v) ? {} : v), items: recs.values(), save,
    ...(save ? {keep_duplicates: keepBox.checked} : {})});
  async function check() {
    const mine = ++seq, rs = rows(), sent = body(false, rs);
    if (!pid()) return;
    if (sent.length > BODY_MAX) { saveBtn.disabled = true; tell("That's more than can be checked at once: save some of it first.", true); return; }
    let j;
    try {
      const r = await api(`profiles/${pid()}/manual`, {method: "POST", body: sent});
      if (r.status === 401) { location.replace("login.html"); return; }
      j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "The server answered " + r.status + ".");
    } catch (e) { if (mine === seq) tell("Couldn't check the rows: " + e.message, true); return; }
    if (mine !== seq) return;   // a newer check is on its way
    show(rs, j.rows || [], j.items || []);
  }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  function show(rs, got, items) {
    const r = recs.show(items);
    okRecs = r.ok; badRecs = r.bad;
    let dup = r.dup;
    const byRow = new Map(got.map(x => [x.row, x]));
    ok = bad = 0;
    rs.forEach((r, i) => {
      const x = byRow.get(i + 1), out = r.querySelector(".man-check");
      r.querySelectorAll("input").forEach(inp => { inp.removeAttribute("aria-invalid"); inp.removeAttribute("aria-describedby"); });
      out.classList.toggle("bad", !!(x && !x.ok));
      if (!x) { out.replaceChildren(); return; }
      if (!x.ok) {
        bad++;
        out.replaceChildren("Can't save this row: it " + x.problems.join("; ") + ".");
        for (const [word, f] of [["test name", "test"], ["date", "date"], ["value", "value"]])
          if (x.problems.some(p => p.includes(word))) {   // the field marked, and what's wrong said with it (#202)
            out.id ||= "man-check-" + (show.n = (show.n || 0) + 1);
            const inp = r.querySelector(`[data-f=${f}]`);
            inp.setAttribute("aria-invalid", "true"); inp.setAttribute("aria-describedby", out.id);
          }
        return;
      }
      ok++;
      if (x.duplicate) dup++;
      const range = x.low != null && x.high != null ? `${x.low} – ${x.high}` : x.high != null ? `under ${x.high}` : x.low != null ? `over ${x.low}` : "";
      const value = x.value != null ? `${x.value}${x.unit ? " " + x.unit : ""}` : `“${x.text}”`;
      out.replaceChildren(icon("check"), " ", el("b", {text: x.name}), ` · ${recordDate(x.date)} · ${value}` + (range ? ` · range ${range}` : ""),
        ...(x.flag ? [" · ", el("span", {class: "abn", text: x.flag === "H" ? "▲ High" : "▼ Low"})] : []),
        ...(x.duplicate ? [" · ", el("span", {class: "dup", text: "already in this profile: it won't be saved again"})] : []),
        ...(x.notes.length ? [` (${x.notes.join("; ")})`] : []));
    });
    $("#manKeepWrap").hidden = !dup;
    if (!dup) keepBox.checked = false;
    const fixing = bad + badRecs, ready = ok + okRecs;
    saveBtn.disabled = saving || !ready || fixing > 0 || recs.busy();
    const what = [ok ? plural(ok, "result", "results") : "", okRecs ? plural(okRecs, "record", "records") : ""].filter(Boolean).join(" and ");
    if (!saving) tell(fixing ? (badRecs ? `${plural(fixing, "entry needs", "entries need")} fixing before saving.` : `${bad === 1 ? "1 row needs" : bad + " rows need"} fixing before saving.`)
      : recs.busy() ? "Uploading the file…" : ready ? `${what} ready to save.` + (dup ? ` ${plural(dup, "is", "are")} already in this profile.` : "") : "", fixing > 0, null, false);   // as you type: said politely
  }

  /* ---------------------------------------------------------------- pasting from a spreadsheet */
  const norm = h => h.toLowerCase().replace(/[^0-9a-z]+/g, " ").trim();
  function flagOf(t) {
    t = norm(t);
    return /^(h|hh|high|hi|a h|critical high)$/.test(t) ? "H" : /^(l|ll|low|lo|critical low)$/.test(t) ? "L" : "";
  }
  box.addEventListener("paste", ev => {
    const target = ev.target.closest("[data-f]"), text = (ev.clipboardData || window.clipboardData).getData("text");
    if (!target || !/[\t\n]/.test(text.trim())) return;   // one cell: pasted as usual
    ev.preventDefault();
    const lines = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n").map(l => l.split("\t"));
    const heads = lines[0].map(h => Object.keys(HEADERS).find(f => HEADERS[f].test(norm(h))) || null);
    let cols;
    if (heads.filter(Boolean).length >= 2) { cols = heads; lines.shift(); }   // a header row says which column is which
    else {   // otherwise the columns follow the fields from the one pasted into
      const start = FIELDS.findIndex(([f]) => f === target.dataset.f);
      cols = lines[0].map((_, i) => (FIELDS[start + i] || [null])[0]);
    }
    let r = target.closest(".man-row"), n = 0;
    for (const cells of lines) {
      if (!cells.some(c => c.trim())) continue;
      if (!r) { r = addRow({}, rows().pop()); if (!r) break; }
      const v = {};
      cols.forEach((f, i) => { if (f && cells[i] != null) v[f] = cells[i].trim(); });
      if ((v.low || v.high) && !v.range) v.range = v.low && v.high ? `${v.low} - ${v.high}` : v.low ? `>=${v.low}` : `<=${v.high}`;
      if ("flag" in v) v.flag = flagOf(v.flag);
      for (const [f] of FIELDS) if (f in v) r.querySelector(`[data-f=${f}]`).value = v[f];
      n++;
      r = r.nextElementSibling;
    }
    tell(`Pasted ${n === 1 ? "1 row" : n + " rows"}; checking…`);
    soon();
  });

  /* ---------------------------------------------------------------- rows, saving */
  box.addEventListener("input", soon);
  box.addEventListener("change", soon);
  box.addEventListener("click", ev => {
    const del = ev.target.closest(".del");
    if (!del) return;
    const r = del.closest(".man-row"), next = r.nextElementSibling || r.previousElementSibling;
    r.remove();
    if (!rows().length) addRow();
    label();
    (next && next.isConnected ? next : rows()[0]).querySelector("input").focus();
    soon();
  });
  $("#manAdd").onclick = () => { const r = addRow(); if (r) r.querySelector("[data-f=test]").focus(); soon(); };

  /* the links below name the profile and the tab in the address (index.html?profile=2#labs, #208); Labs also charts
     the labs just typed in, added to the ones its saved view shows (core.js: ht-state:<id>) */
  const recordsLink = (p, tab) => `index.html?profile=${p}#${tab}`;
  function openTrends(p, names) {
    try {
      const k = "ht-state:" + p, s = JSON.parse(localStorage.getItem(k) || "{}") || {};
      localStorage.setItem(k, JSON.stringify({...s, sel: [...new Set([...(s.sel || []), ...names])], v: 2}));
    } catch (e) {}
  }
  saveBtn.onclick = async () => {
    const p = pid(), rs = rows(), sent = body(true, rs);
    saving = true; saveBtn.disabled = true; $("#profile").disabled = true;
    tell("Saving…");
    try {
      const r = await api(`profiles/${p}/manual`, {method: "POST", body: sent});
      if (r.status === 401) { location.replace("login.html"); return; }
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        saving = false;
        if (j.rows) show(rs, j.rows, j.items);
        tell(typeof j.detail === "string" ? j.detail : "The server answered " + r.status + ". Nothing was saved.", true);
        return;
      }
      // counts.results counts vitals too (they're charted as series): what was typed is in counts.typed
      const typed = (j.counts || {}).typed || {}, n = typed.labs || 0, names = j.series || [];
      const others = Object.entries(typed).filter(([k]) => k !== "labs").reduce((a, [, c]) => a + c, 0);
      // #224: readings taken at home are on Body and activity, a height in About this person; the rest on the timeline
      const HEALTH = ["home", "home_bp", "home_glucose", "home_temp", "home_spo2", "workout"];
      const onHealth = HEALTH.some(k => typed[k]), onTimeline = Object.keys(typed).some(k => k !== "labs" && k !== "height" && !HEALTH.includes(k));
      box.replaceChildren(); addRow({date: rs.length ? valueOf(rs[rs.length - 1]).date : today()});
      recs.saved(); keepBox.checked = false; $("#manKeepWrap").hidden = true;
      saving = false; ok = bad = okRecs = badRecs = 0;
      const links = [names.length ? el("a", {href: recordsLink(p, "labs"), text: "See them on Labs", onclick: () => openTrends(p, names)}) : null,
        onTimeline ? el("a", {href: recordsLink(p, "timeline"), text: "See them on the timeline"}) : null,
        onHealth ? el("a", {href: recordsLink(p, "health"), text: "See them on Body and activity"}) : null,
        typed.height ? el("a", {href: `sharing.html?profile=${p}#about`, text: "See the height history"}) : null].filter(Boolean);
      sayIn(msg, [`Saved ${[n ? plural(n, "result", "results") : "", others ? plural(others, "record", "records") : ""].filter(Boolean).join(" and ") || "nothing new"}.`
        + (j.skipped ? ` ${plural(j.skipped, "was", "were")} in this profile already and left out.` : ""),
        ...links.flatMap(a => [" ", a])]);
    } catch (e) {
      saving = false;
      tell("Couldn't save: " + e.message + " Nothing was saved.", true);
    } finally {
      saving = false; $("#profile").disabled = false; saveBtn.disabled = true;
    }
  };

  /* ---------------------------------------------------------------- start */
  addRow();
  recs.onchange(soon);
  keepBox.addEventListener("change", () => { if (!saving) soon(); });
  $("#profile").addEventListener("change", () => { recs.reset(); soon(); });
  (async () => {   // the suggestions for the test field
    try {
      const r = await api("lab-names");
      if (r.ok) $("#labNames").replaceChildren(...(await r.json()).map(n => el("option", {value: n})));
    } catch (e) {}
  })();
})();
