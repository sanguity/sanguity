/* Sanguity · import page, "Type them in" tab (#27): lab results typed in by hand, one row each, and the other kinds of
   record typed into forms of their own (import-records.js), saved together as one import.
   Rows can be added, removed and pasted from a spreadsheet (several cells at once fill the rows from the one pasted
   into, by the copied header if it has one, else column by column). Each change is checked by the server, which reads
   the rows exactly as the CSV importer reads a spreadsheet's (POST profiles/{id}/manual, save false), and the row
   says what it became: the test it's charted as, the value, the range and the flag. Save stores them (save true).
   Test names are suggested from lab-aliases.json's canonical names (GET lab-names). Rows and records the profile
   has already are pointed out, and left out of the save unless "Save again what's already there" is ticked; a result
   here typed again with what it lacks (its range, a flag) is saved, its row saying so (#868).
   The date and time are the browser's pickers (#285). What a picker can't hold (a date pasted as 11/02/2026) is kept
   on it (data-raw) and sent as it was, so the server reads it as it reads a spreadsheet's; once read, the picker shows it.
   Plain script after util.js, import-jobs.js (its Undo, #508) and import.js; its own names stay inside this function. */
"use strict";
(function () {
  const FIELDS = [   // [field, caption, placeholder]: nothing that could pass for a value typed in
    ["date", "Date"], ["time", "Time (optional)"], ["test", "Test"], ["value", "Value"],
    ["unit", "Unit"], ["range", "Range", "low - high"], ["flag", "Flag"], ["lab", "Lab", "optional"]];
  const MAX = 200;   // rows the server takes at once
  // a typed result already here, kept with its own row, by what it brings (#868: the server's "copy"); a "row": the
  // result here from another report of it, the two read as one
  const COPY_SAID = {range: "already in this profile: added again with its range, so neither is lost",
    flag: "already in this profile: added again with its flag, so neither is lost",
    row: "already in this profile from another report: added again as typed, shown once on Labs"};
  // headers a pasted spreadsheet may have, as csv_labs.FIELDS spells them (the common ones)
  const HEADERS = {date: /^(date|collected|collection date|draw date|test date|result date|specimen date|date collected)$/,
    time: /^(time|collection time|draw time)$/, test: /^(test|test name|analyte|component|name|lab test|parameter|marker)$/,
    value: /^(value|result|result value|results|reading|your value|level)$/, unit: /^(unit|units|uom|unit of measure)$/,
    range: /^(range|reference range|ref range|reference interval|normal range|reference)$/,
    low: /^(low|ref low|range low|lower limit|min|minimum|normal low)$/, high: /^(high|ref high|range high|upper limit|max|maximum|normal high)$/,
    flag: /^(flag|flags|abnormal flag|abnormal|abn|abnormal indicator|out of range|result flag|interpretation|h l|hl)$/,
    lab: /^(lab|laboratory|performing lab|facility)$/};
  const box = $("#manRows"), msg = $("#manMsg"), saveBtn = $("#manSave"), recs = window.sanguityRecords;
  const keepBox = $("#manKeep"), labs = $("#manLabs"), BODY_MAX = 60000;   // nginx refuses API bodies over 64 KB
  let timer = null, seq = 0, ok = 0, bad = 0, okRecs = 0, badRecs = 0, saving = false;
  // a save that failed: its words stay until something's changed, past a check scheduled before it (#387)
  let failed = false;

  const pid = () => +$("#profile").value;
  const tell = (text, isBad = false, link = null, urgent = isBad) => {   // urgent: said at once (sayIn, util.js)
    sayIn(msg, [text, ...(link ? [" ", link] : [])], urgent, {repeat: urgent}); msg.classList.toggle("bad", isBad);
    delete msg.dataset.savedLine;   // (no longer the line of a save, for an Undo elsewhere to change)
  };
  const today = zToday;   // dates.js
  const rows = () => [...box.querySelectorAll(".man-row")];
  const input = (r, f) => r.querySelector(`[data-f=${f}]`);
  const valueOf = r => Object.fromEntries(FIELDS.map(([f]) => { const i = input(r, f); return [f, i.value.trim() || i.dataset.raw || ""]; }));
  function put(i, v) {   // a picker refuses what isn't its own format: kept beside it for the server to read
    i.value = v; delete i.dataset.raw;
    if (v && !i.value) i.dataset.raw = v;
  }
  // a row with only what a new row is given (date, time, lab) is still empty: nothing of it is sent
  const blank = v => !["test", "value", "unit", "range", "flag"].some(f => v[f]);
  // but it's been typed into once any of its fields differs from what it was given, its date or lab too (#384)
  const typedIn = r => JSON.stringify(valueOf(r)) !== r.dataset.given;

  function field([f, cap, ph]) {
    const input = f === "flag"
      ? el("select", {"data-f": f}, el("option", {value: "", text: "–"}), el("option", {value: "H", text: "High"}), el("option", {value: "L", text: "Low"}))
      : f === "date" ? el("input", {"data-f": f, type: "date", min: "1900-01-01", max: "2100-12-31"})
      : f === "time" ? el("input", {"data-f": f, type: "time"})
      : el("input", {"data-f": f, type: "text", placeholder: ph, autocomplete: "off", spellcheck: "false",
                     inputmode: f === "value" ? "decimal" : null, list: f === "test" ? "labNames" : null,
                     maxlength: {test: 120, value: 60, unit: 30, range: 60, lab: 120}[f]});
    return el("label", {class: "f-" + f}, el("span", {class: "cap", text: cap}), input);
  }
  function addRow(v = {}, after = null) {
    if (rows().length >= MAX) { tell(`At most ${MAX} rows at a time: save these first.`, true); return null; }
    const last = after || rows().pop(), from = last ? valueOf(last) : {};
    const r = el("div", {class: "man-row", role: "group"}, FIELDS.map(field),
      el("button", {type: "button", class: "del btn quiet", "data-tip": "Remove this row", "aria-label": "Remove this row"}, icon("x")),
      el("div", {class: "man-check", "aria-live": "polite"}));
    // a new row starts on the last one's date, time and lab: a report's results share them
    for (const [f] of FIELDS) put(input(r, f), v[f] ?? (["date", "time", "lab"].includes(f) ? from[f] || (f === "date" ? today() : "") : ""));
    r.dataset.given = JSON.stringify(valueOf(r));
    if (after) after.after(r); else box.append(r);
    label();
    return r;
  }
  function label() {   // each row's group is named by its number, so "Remove this row" and the checks say which
    rows().forEach((r, i) => r.setAttribute("aria-label", `Result ${i + 1}`));
  }

  /* ---------------------------------------------------------------- checking as you go */
  function soon() { failed = false; clearTimeout(timer); timer = setTimeout(check, 350); }
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
    } catch (e) { if (mine === seq && !failed) tell("Couldn't check the rows: " + e.message, true); return; }
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
        // (a date is picked, not written: the server's words for a date pasted as 03/04/2026 say so here.) A time pasted
        // that its picker can't show is read with the date: said here, as the date is in the problem
        const rawTime = input(r, "time").dataset.raw;
        out.replaceChildren("Can't save this row: it " + x.problems.join("; ").replace("write it as YYYY-MM-DD", "pick it in its box") + (rawTime ? ` (the time pasted: “${rawTime}”)` : "") + ".");
        for (const [word, f] of [["test name", "test"], ["date", "date"], ["value", "value"], ["date", rawTime ? "time" : ""]])
          if (f && x.problems.some(p => p.includes(word))) {   // the field marked, and what's wrong said with it (#202)
            out.id ||= "man-check-" + (show.n = (show.n || 0) + 1);
            const inp = r.querySelector(`[data-f=${f}]`);
            inp.setAttribute("aria-invalid", "true"); inp.setAttribute("aria-describedby", out.id);
          }
        return;
      }
      ok++;
      if (x.duplicate) dup++;
      // a date or time the pickers couldn't hold, read: shown in them now (x.date is "YYYY-MM-DD" or "YYYY-MM-DD HH:MM")
      for (const [f, v] of [["date", x.date.slice(0, 10)], ["time", x.date.slice(11, 16)]])
        if (input(r, f).dataset.raw) put(input(r, f), v);
      // its range and flag as Labs will say them (#826): "3.5–5.1", "< 5" (5 left out of it, the edge the server read)
      // or "≤ 5" (ranges.js rangeWords); ▲ High, ▼ Low, or ◆ Flagged for a flag that says neither (ranges.js flagWord);
      // a value given as a bound said as one, by the comparator the server read (#875: "=<5" is "≤ 5 U/L", not "5 U/L")
      const range = rangeWords([null, x.value, x.low, x.high, x.flag, null, null, x.edge]);
      const value = x.value != null ? boundWords(x.cmp, x.value) + (x.unit ? " " + x.unit : "") : `“${x.text}”`;
      out.replaceChildren(icon("check"), " ", el("b", {text: x.name}), ` · ${recordDate(x.date)} · ${value}` + (range ? ` · range ${range}` : ""),
        ...(x.flag ? [" · ", el("span", {class: "abn", text: flagWord(x.flag)})] : []),
        ...(x.duplicate ? [" · ", el("span", {class: "dup", text: "already in this profile: it won't be saved again"})] : []),
        // already here and kept, with its own row, by what it brings (#868: the server's preview.find, an upload's rule)
        ...(x.copy ? [" · ", el("span", {class: "dup", text: COPY_SAID[x.copy] || COPY_SAID.row})] : []),
        ...(x.notes.length ? [` (${x.notes.join("; ")})`] : []));
    });
    $("#manKeepWrap").hidden = !dup;
    if (!dup) keepBox.checked = false;
    const fixing = bad + badRecs, ready = ok + okRecs;
    saveBtn.disabled = saving || !ready || fixing > 0 || recs.busy();
    const what = [ok ? plural(ok, "result", "results") : "", okRecs ? plural(okRecs, "record", "records") : ""].filter(Boolean).join(" and ");
    // (after a failed save, Save enabled again but the failure's words left: a check scheduled as Save was clicked, #387)
    if (!saving && !failed) tell(fixing ? (badRecs ? `${plural(fixing, "entry needs", "entries need")} fixing before saving.` : `${bad === 1 ? "1 row needs" : bad + " rows need"} fixing before saving.`)
      : recs.busy() ? "Uploading the file…" : ready ? `${what} ready to save.` + (dup ? ` ${plural(dup, "is", "are")} already in this profile.` : "") : "", fixing > 0, null, false);   // as you type: said politely
  }

  /* ---------------------------------------------------------------- pasting from a spreadsheet */
  const norm = h => h.toLowerCase().replace(/[^0-9a-z]+/g, " ").trim();
  // a flag pasted is kept as printed (#835): High or Low where the select has it, else an option of its own ("Abnormal",
  // "Yes"), so the server reads it by the one rule (common.report_flag) as it reads a spreadsheet's Flag column, and the
  // row's check says what it became; only High and Low were kept, so "A" or "Abnormal" was saved with no flag
  function putFlag(s, t) {
    t = t.slice(0, 60);   // (the server takes 60 characters)
    const o = [...s.options].find(o => [o.value, o.textContent].some(x => x && x.toLowerCase() === t.toLowerCase()));
    if (!o && t) s.append(el("option", {value: t, text: t}));
    s.value = o ? o.value : t;
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
      for (const [f] of FIELDS) if (f in v) (f === "flag" ? putFlag : put)(input(r, f), v[f]);
      n++;
      r = r.nextElementSibling;
    }
    tell(`Pasted ${n === 1 ? "1 row" : n + " rows"}; checking…`);
    soon();
  });

  /* ---------------------------------------------------------------- rows, saving */
  const typed = ev => { delete ev.target.dataset.raw; soon(); };   // a picker changed: what it shows is what's sent
  box.addEventListener("input", typed);
  box.addEventListener("change", typed);
  box.addEventListener("click", ev => {
    const del = ev.target.closest(".del");
    if (!del) return;
    const r = del.closest(".man-row"), next = r.nextElementSibling || r.previousElementSibling;
    r.remove();
    label();
    // the last row removed puts lab results away, like any other record's form; its chip brings it back
    if (!rows().length) { labs.hidden = true; $("#manKind-labs").focus(); }
    else (next && next.isConnected ? next : rows()[0]).querySelector("input").focus();
    soon();
  });
  /* Lab results is one kind of record among the others in "Add a record". The page starts with its empty row (the
     first in the list), and adding another kind before anything is typed into its rows puts it away, so the form shows
     only what's being typed in; choosing Lab results again brings it back, after the forms added before it, as any
     kind's form is added (#285; import-records.js calls this first) */
  window.sanguityLabRows = {choose(kind) {
    if (kind === "labs") {   // shown again with a row to type in: true, nothing more to do
      if (!labs.hidden) return false;
      labs.parentElement.append(labs); labs.hidden = false;
      const r = rows()[0] || addRow();
      if (r) r.querySelector("[data-f=test]").focus();
      soon();
      return true;
    }
    if (!labs.hidden && !rows().some(typedIn)) { box.replaceChildren(); labs.hidden = true; soon(); }
    return false;
  }};
  $("#manAdd").onclick = () => { const r = addRow(); if (r) r.querySelector("[data-f=test]").focus(); soon(); };

  /* the links below name the profile and the tab in the address (index.html?profile=2#labs-chart, #208); Labs charts
     the labs just typed in, added to the ones its saved view shows (core.js: ht-state:<id>), at its charts' own address
     whichever view Labs opens on (#1026) */
  const recordsLink = (p, tab) => `index.html?profile=${p}#${tab}`;
  function openTrends(p, names) {
    try {
      const k = "ht-state:" + p, s = JSON.parse(localStorage.getItem(k) || "{}") || {};
      localStorage.setItem(k, JSON.stringify({...s, sel: [...new Set([...(s.sel || []), ...names])], v: 2}));
    } catch (e) {}
  }
  saveBtn.onclick = async () => {
    const p = pid(), rs = rows(), sent = body(true, rs), wasOn = document.activeElement === saveBtn;
    let retry = false;   // failed, and nothing in the rows to fix: Save stays on to try again (a network failure, a 500)
    saving = true; saveBtn.disabled = true; $("#profile").disabled = true;
    tell("Saving…");
    try {
      const r = await api(`profiles/${p}/manual`, {method: "POST", body: sent});
      if (r.status === 401) { location.replace("login.html"); return; }
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        saving = false;
        if (j.rows) show(rs, j.rows, j.items); else retry = true;   // the rows' problems set Save themselves (show)
        tell(typeof j.detail === "string" ? j.detail : "The server answered " + r.status + ". Nothing was saved.", true);
        failed = true;
        return;
      }
      // counts.results counts vitals too (they're charted as series): what was typed is in counts.typed
      const typed = (j.counts || {}).typed || {}, n = typed.labs || 0, names = j.series || [];
      const others = Object.entries(typed).filter(([k]) => k !== "labs").reduce((a, [, c]) => a + c, 0);
      // #224: readings taken at home are on Body and activity, a height in About this person; payments and claims on Costs,
      // problems and upcoming appointments on the Overview (#387); the rest on the timeline
      const HEALTH = ["home", "home_bp", "home_glucose", "home_temp", "home_spo2", "workout"], COSTS = ["payment", "claim"], OVERVIEW = ["problem", "upcoming"];
      const any = ks => ks.some(k => typed[k]), elsewhere = ["labs", "height", ...HEALTH, ...COSTS, ...OVERVIEW];
      const onHealth = any(HEALTH), onCosts = any(COSTS), onOverview = any(OVERVIEW), onTimeline = Object.keys(typed).some(k => !elsewhere.includes(k));
      box.replaceChildren(); labs.hidden = false; addRow({date: rs.length ? valueOf(rs[rs.length - 1]).date : today()});
      recs.saved(); keepBox.checked = false; $("#manKeepWrap").hidden = true;   // as on load
      saving = false; ok = bad = okRecs = badRecs = 0;
      // a check of what was there before (a field's change event as Save was clicked, 350 ms) has nothing to say about
      // the new form: not run, or not shown, so it doesn't clear the saved line below (#384)
      clearTimeout(timer); seq++;
      const links = [names.length ? el("a", {href: recordsLink(p, "labs-chart"), text: "See them on Labs", onclick: () => openTrends(p, names)}) : null,
        onTimeline ? el("a", {href: recordsLink(p, "timeline"), text: "See them on the timeline"}) : null,
        onCosts ? el("a", {href: recordsLink(p, typed.payment ? "costs-paid" : "costs-claims"), text: "See them on Costs"}) : null,   // a payment's on Payments and bills (#443), a claim's on Claims (#1163)
        onOverview ? el("a", {href: recordsLink(p, "overview"), text: "See them on the Overview"}) : null,
        onHealth ? el("a", {href: recordsLink(p, "health"), text: "See them on Body and activity"}) : null,
        typed.height ? el("a", {href: `settings.html?profile=${p}#about`, text: "See the height history"}) : null].filter(Boolean);
      const what = [n ? plural(n, "result", "results") : "", others ? plural(others, "record", "records") : ""].filter(Boolean).join(" and ");
      const skipped = j.skipped ? ` ${plural(j.skipped, "was", "were")} in this profile already and left out.` : "";
      const said = `Saved ${what || "nothing new"}.${skipped}`;
      // shown here with its links, said by a toast (#263) with the first of them (Open Labs charting what was typed,
      // as its link does, #360) and an Undo, which asks first in Import history's words: the form is cleared already
      sayIn(msg, [said, ...links.flatMap(a => [" ", a])], false, {say: false});
      // the toast names what it saved, so two saves' toasts aren't both "Saved 1 result." (#522): the tests as typed in
      // the rows saved (j.rows, numbered as sent), and the kinds of the rest ("Saved 1 result: Sodium.", "Saved 1
      // record: 1 payment.")
      const kept = JSON.parse(sent).keep_duplicates, tests = [...new Set((j.rows || []).filter(d => d.ok && d.name && (kept || !d.duplicate))
        .map(d => rs[d.row - 1] && valueOf(rs[d.row - 1]).test).filter(Boolean))];
      const rest = sanguityImport.importCounts({typed: Object.fromEntries(Object.entries(typed).filter(([k]) => k !== "labs"))});
      const named = [n ? (tests.length > 3 ? tests.slice(0, 3).join(", ") + ` and ${tests.length - 3} more` : tests.join(", ")) : "",
        others && rest !== "No records" ? rest : ""].filter(Boolean).join("; ");
      const t = toast(named && what ? `Saved ${what}: ${named}.${skipped}` : said, {actions: [...links.slice(0, 1).map(a => ({text: a.textContent.replace(/^See them on /, "Open "), href: a.href,
          onClick: names.length ? () => openTrends(p, names) : null})),   // (the first link is Labs', when there are labs)
        // (asked as Import history and an upload's toast ask it, #508: import-jobs.js undoAsk; the page told, Import
        // history read again; focus on the line saying so, not on a toast)
        ...(j.import_id != null && (n || others) ? [{text: "Undo…", onClick: async () => {
          // the line is this save's still, not a later one's (#522: an older toast's Undo said the latest was gone)
          const ours = msg.dataset.savedLine === String(j.import_id);
          const said = await sanguityImport.undoAsk(p, j.import_id, {origin: "manual", label: "Typed in", counts: j.counts, upload: null});
          if (!said) return false;
          if (ours) sayIn(msg, "Undone: what was saved is gone from the profile.", false, {say: false});   // (as undoneElsewhere said it)
          sanguityImport.undoneFocus(ours ? msg : null, said);
          toast("Undone: what was saved is gone from the profile.");
        }}] : [])]});
      if (j.import_id != null) {
        msg.dataset.savedLine = j.import_id;   // undone elsewhere, the line says so (import-jobs.js undoneElsewhere, #517)
        sanguityImport.undoable(t, j.import_id);   // its Undo taken away once it's undone elsewhere (#513)
        sanguityImport.told(p, j.import_id, false);   // Import history lists it at once, with its Undo (#508)
      }
    } catch (e) {
      saving = false;
      tell("Couldn't save: " + e.message + " Nothing was saved.", true);
      failed = retry = true;
    } finally {
      // saved: off until something's typed; failed for no fault in the rows: on, so it can be tried again as it is
      saving = false; $("#profile").disabled = false; if (!failed) saveBtn.disabled = true; else if (retry) saveBtn.disabled = false;
      // Save disabled itself, so focus fell to <body>: to the line that says what happened, its links next (#384), and
      // the line scrolled clear of the toast just shown (scroll-padding-bottom, from the toasts' height once they're laid
      // out: the frame after next, util.js), which the focus alone doesn't do: the line under it counts as in view (#387)
      if (wasOn && (document.activeElement === document.body || document.activeElement === saveBtn)) {
        msg.focus({preventScroll: true});
        requestAnimationFrame(() => requestAnimationFrame(() => { if (document.activeElement === msg) msg.scrollIntoView({block: "nearest"}); }));
      }
    }
  };

  /* ---------------------------------------------------------------- start */
  // arriving with another kind's form already there (Costs' "Type in a payment", sources.html#payment, import-records.js):
  // lab results start put away, as if that kind had been added (#384)
  if (recs.count()) labs.hidden = true; else addRow();
  recs.onchange(soon);
  // leaving the page with what's typed in not saved asks first, as Settings does for its unsaved sections (#388): a lab
  // row changed from what it was given, or a record's form from what it started with (values() sends only its kind)
  addEventListener("beforeunload", ev => {
    if (!sgLeaving && (rows().some(typedIn) || recs.values().some(v => Object.keys(v).length > 1))) { ev.preventDefault(); ev.returnValue = ""; }
  });
  keepBox.addEventListener("change", () => { if (!saving) soon(); });
  $("#profile").addEventListener("change", () => { recs.reset(); soon(); });
  (async () => {   // the suggestions for the test field
    try {
      const r = await api("lab-names");
      if (r.ok) $("#labNames").replaceChildren(...(await r.json()).map(n => el("option", {value: n})));
    } catch (e) {}
  })();
})();
