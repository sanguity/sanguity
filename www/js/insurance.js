/* Sanguity · insurance cards (#1137): Settings → This person → Insurance cards, the card #insurance. Each health plan
   of the profile with what its card says and a photo of each side. server/insurance.py keeps them
   (routes_insurance.py): owners and editors add, change and remove them, everyone the profile is shared with sees
   them. A share link shows them only when its maker ticks Insurance cards; Export everything has them.

   Its dialog (#1168) has the photos too, while a plan is added or later: each side taken with the phone's camera or
   chosen from its photos, the card found in it, straightened and cropped (cardcrop.js; its corners adjusted by hand
   when it's missed), shown as it will be saved, with Retake. Read the card sends those photos to the server's text
   recognition (when it has it) and the claims imported suggest a plan: each suggestion is shown under its field to
   use or leave, and none goes in by itself. Its type and order, the policyholder, its dates (an ended plan is listed
   under Past), pharmacy numbers, payer ID and website; what it's linked to on Costs (its plan years, its claims'
   insurer); what the emergency card says of it. Show at the desk (ins-desk.js) shows it full screen.
   drawInsurance(p): the card, drawn for the profile shown (settings.js). Plain script; all files share one scope. */
"use strict";
const INS_TYPES = ["image/jpeg", "image/png", "image/webp"], INS_PHONES = 4;
const INS_SIDE = {front: "Front", back: "Back"};
const INS_KIND = {medical: "Medical", dental: "Dental", vision: "Vision", pharmacy: "Pharmacy"};
const INS_ORDER = {primary: "Primary", secondary: "Secondary"};
const INS_REL = {self: "Self", spouse: "Spouse or partner", child: "Child", other: "Other"};
const INS_CARD = {kind: "Plan type and order", holder: "Policyholder", rx: "Pharmacy numbers", payer: "Payer ID", phones: "Phone numbers"};
const INS_RX = [["rxbin", "RxBIN"], ["rxpcn", "RxPCN"], ["rxgroup", "RxGroup"], ["payer", "Payer ID"]];
let INS_P = null, INS_AT = 0;   // the profile drawn; where the row acted on was
const insPast = c => !!c.end && c.end < zToday();
const insKind = c => [INS_KIND[c.kind], INS_ORDER[c.order]?.toLowerCase()].filter(Boolean).join(", ").replace(/^./, m => m.toUpperCase());
async function insCall(path, method = "GET", body, headers) {
  const r = await api(path, {method, body: body === undefined || body instanceof Blob ? body : JSON.stringify(body), headers});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `${r.status} ${r.statusText}`);
  return j;
}

/* the dialog: a plan's fields and its card's photos (plan: the one changed, or null for a new one). ctx: {canRead (the
   server reads cards), rec (GET .../insurance/suggestions, or null)} -> {fields, sides: {front, back: {blob, drop,
   saved}}}, or null */
async function insForm(p, plan, ctx) {
  const id = "insF" + (insForm.n = (insForm.n || 0) + 1), F = {}, rec = ctx.rec || {plans: [], payers: [], years: []};
  const used = e => e.addEventListener(e.tagName === "SELECT" ? "change" : "input", () => e.classList.remove("ins-used"));
  const field = (name, label, value, attrs = {}) => {
    const input = F[name] = el("input", {type: "text", id: `${id}-${name}`, value: value || "", autocomplete: "off", spellcheck: "false", ...attrs});
    used(input);
    return el("div", {class: "ins-f"}, el("label", {for: input.id, text: label}), input);
  };
  const choose = (name, label, value, opts) => {
    const s = F[name] = el("select", {id: `${id}-${name}`}, el("option", {value: "", text: "Not said"}),
      ...Object.entries(opts).map(([k, t]) => el("option", {value: k, text: t, selected: k === value || null})));
    used(s);
    return el("div", {class: "ins-f"}, el("label", {for: s.id, text: label}), s);
  };
  const box = (legend, ...kids) => el("fieldset", {class: "ins-set"}, el("legend", {text: legend}), ...kids);
  const said = el("p", {class: "ins-said", id: id + "-said"});
  // ---- the photos
  const phone = matchMedia("(pointer: coarse)").matches;
  const sides = Object.fromEntries(["front", "back"].map(s => [s, {saved: !!plan?.[s], blob: null, crop: null, drop: false, url: null}]));
  const blocks = Object.fromEntries(["front", "back"].map(s => [s, el("div", {class: "ins-side", role: "group", "aria-labelledby": `${id}-${s}-hd`})]));
  const savedUrl = s => `api/profiles/${p.id}/insurance/${plan.id}/${s}?v=${plan.changed || plan.at}`;
  const what = s => `${s} of the card`;
  const cropped = async (s, input) => {
    const st = sides[s];
    let got;
    try { got = await ccCrop(input, what(s)); } catch (e) { sayIn(said, e.message, true); return; }
    if (!got) return;
    Object.assign(st, {blob: got.blob, crop: got, drop: false, url: await dataUrl(got.blob)});
    sayIn(said, `The ${what(s)} will be saved as shown.`);
  };
  const picker = (s, text, capture) => {
    const input = el("input", {type: "file", accept: INS_TYPES.join(","), capture: capture ? "environment" : null, id: `${id}-${s}${capture ? "-cam" : "-file"}`,
      "aria-label": `${text.replace("…", "")}: the ${what(s)}`});
    input.onchange = async () => {
      const f = input.files[0];
      input.value = "";
      if (!f) return;
      if (f.type && !INS_TYPES.includes(f.type)) { sayIn(said, `${f.name} isn't a JPEG, PNG or WebP picture.`, true); return; }
      await cropped(s, {file: f});
      drawSide(s, true);
    };
    return el("label", {class: "btn small pick ins-pick"}, text, input);
  };
  const sbtn = (text, label, run) => el("button", {type: "button", class: "btn small", text, "aria-label": label, onclick: run});
  function drawSide(s, focus) {
    const st = sides[s], shown = st.blob ? st.url : st.saved && !st.drop ? savedUrl(s) : null;
    const acts = !shown ? [phone ? picker(s, "Take a photo…", true) : null, picker(s, phone ? "Choose a photo…" : "Add a photo…")]
      : [phone ? picker(s, "Retake…", true) : null, picker(s, "Choose another…"),
        st.crop ? sbtn("Adjust corners…", `Adjust the corners of the ${what(s)}`, async () => { await cropped(s, st.crop); drawSide(s, true); }) : null,
        sbtn("Take off", `Take off the photo of the ${what(s)}`, () => {
          if (st.blob) Object.assign(st, {blob: null, crop: null, url: null}); else st.drop = true;
          sayIn(said, st.saved && st.drop ? `The photo of the ${what(s)} is taken off when you save.` : `Took off the new photo of the ${what(s)}.`);
          drawSide(s, true);
        })];
    if (!shown && st.drop) acts.push(sbtn("Keep the photo", `Keep the photo of the ${what(s)}`, () => { st.drop = false; drawSide(s, true); }));
    blocks[s].replaceChildren(el("h3", {class: "sub", id: `${id}-${s}-hd`, text: INS_SIDE[s]}),
      shown ? el("img", {class: "ins-pv", src: shown, alt: `${INS_SIDE[s]} of the card${st.blob ? ", as it will be saved" : ""}`})
        : el("p", {class: "muted", text: st.drop ? "Taken off when you save." : "No photo yet."}),
      el("div", {class: "row"}, ...acts));
    if (focus) blocks[s].querySelector("button, input")?.focus();
  }
  ["front", "back"].forEach(s => drawSide(s));
  // ---- suggestions: each under its field, used or left; nothing goes in by itself
  const label = k => document.querySelector(`label[for="${id}-${k}"]`)?.textContent || k;
  const shownAs = (k, v) => k === "kind" ? INS_KIND[v] : k === "relation" ? INS_REL[v] : k === "start" || k === "end" ? fmtD(v) : v;
  const useAll = el("button", {type: "button", class: "btn small", hidden: "", text: "Use every suggestion", onclick: () => {
    [...form().querySelectorAll(".ins-sug .ins-use")].forEach(b => b.click()); useAll.hidden = true; sayIn(said, "Used every suggestion: check each field before you save.");
  }});
  const form = () => blocks.front.closest(".sg-ask-msg");
  function chip(key, anchor, target, text, from, apply, what) {
    const cid = `${id}-${key}-sug`;
    form().querySelector("#" + cid)?.remove();
    const done = () => { c.remove(); target.setAttribute("aria-describedby", (target.getAttribute("aria-describedby") || "").replace(cid, "").trim()); if (!form().querySelector(".ins-sug")) useAll.hidden = true; target.focus(); };
    const c = el("div", {class: "ins-sug", id: cid}, el("span", {}, from + ": ", el("b", {text})),
      el("span", {class: "acts"},
        el("button", {type: "button", class: "btn small ins-use", text: "Use", "aria-label": `Use ${text} for ${what}`, onclick: () => { apply(); target.classList.add("ins-used"); done(); }}),
        el("button", {type: "button", class: "btn small", text: "Not this", "aria-label": `Leave ${what} as it is`, onclick: done})));
    anchor.after(c);
    target.setAttribute("aria-describedby", ((target.getAttribute("aria-describedby") || "") + " " + cid).trim());
  }
  function offer(found, from) {   // -> how many suggestions it shows
    let n = 0;
    for (const [k, v] of Object.entries(found || {})) {
      if (k === "phones" && v.length) {
        const have = phoneRows.map(r => r.num.value.replace(/\D/g, "")), add = v.filter(x => !have.includes(x.number.replace(/\D/g, "")));
        if (!add.length) continue;
        chip("phones", phoneList, phoneRows[0].num, add.map(x => (x.label ? x.label + " " : "") + x.number).join("; "), from, () => {
          for (const x of add) {
            const r = phoneRows.find(r => !r.num.value.trim()) || (phoneRows.length < INS_PHONES ? addPhone() : null);
            if (!r) break;
            r.l.value = r.l.value || x.label; r.num.value = x.number; r.num.classList.add("ins-used");
          }
        }, "the phone numbers");
        n++;
      } else if (k === "payers" && linkSet) {
        const boxes = [...linkSet.querySelectorAll("input[data-payer]")].filter(b => v.includes(b.dataset.payer) && !b.checked);
        if (!boxes.length) continue;
        chip("payers", linkSet.querySelector("legend"), boxes[0], `link to the claims from ${v.join(", ")}`, from, () => boxes.forEach(b => { b.checked = true; }), "what it's linked to");
        n++;
      } else if (F[k] && v != null && v !== "" && String(v) !== F[k].value) {
        if (F[k].tagName === "SELECT" && ![...F[k].options].some(o => o.value === v)) continue;
        chip(k, F[k], F[k], shownAs(k, v), from, () => { F[k].value = v; }, label(k).replace(/ \(optional\)$/, ""));
        n++;
      }
    }
    useAll.hidden = n < 2;
    return n;
  }
  // ---- read the card (#1129's text recognition, on the server)
  const readBtn = ctx.canRead ? el("button", {type: "button", class: "btn small", text: "Read the card", onclick: async () => {
    const blobs = [];
    try {
      for (const s of ["front", "back"]) {
        const st = sides[s];
        if (st.blob) blobs.push(st.blob);
        else if (st.saved && !st.drop) blobs.push(await (await api(`profiles/${p.id}/insurance/${plan.id}/${s}`)).blob());
      }
    } catch (e) { sayIn(said, e.message, true); return; }
    if (!blobs.length) { sayIn(said, "Add a photo of the card first.", true); return; }
    readBtn.disabled = true;
    sayIn(said, "Reading the card…");
    const found = {};
    let state = "none";
    try {
      for (const b of blobs) {
        const j = await insCall(`profiles/${p.id}/insurance/read`, "POST", b, {"Content-Type": b.type || "application/octet-stream"});
        if (j.state === "off") { state = "off"; break; }
        if (j.state === "found") {
          state = "found";
          for (const [k, v] of Object.entries(j.found))
            if (k !== "phones") found[k] ??= v;
            else found.phones = [...found.phones || [], ...v.filter(x => !(found.phones || []).some(y => y.number === x.number))];
        }
      }
    } catch (e) { sayIn(said, e.message, true); return; } finally { readBtn.disabled = false; }
    if (state === "off") { sayIn(said, "Text recognition isn't on for this server, so the card can't be read here. Type what it says."); return; }
    const n = offer(found, "From the card");
    sayIn(said, n ? `The card suggests ${n} ${n === 1 ? "field" : "fields"}, each marked below with Use. Check it against the card.` : "Nothing on the card could be read as one of its fields. Type what it says.");
  }}) : null;
  // ---- the fields
  const name = field("name", "Plan's name", plan?.name, {maxlength: 120, required: true});
  const ids = [field("member", "Member ID (optional)", plan?.member, {maxlength: 60}), field("group", "Group number (optional)", plan?.group, {maxlength: 60})];
  const have = plan?.phones || [], phoneRows = [], phoneList = el("div", {class: "ins-phones"});
  const more = el("button", {type: "button", class: "btn small", text: "Add another number", onclick: () => { addPhone().l.focus(); }});
  function addPhone(x = {}) {
    const n = phoneRows.length + 1;
    const lw = field(`pl${n}`, `Phone ${n}: what it's for (optional)`, x.label, {maxlength: 40, placeholder: "e.g. Member services"});
    const nw = field(`pn${n}`, `Phone ${n}: number (optional)`, x.number, {maxlength: 40, type: "tel", inputmode: "tel"});
    const r = {l: F[`pl${n}`], num: F[`pn${n}`]};
    phoneRows.push(r);
    phoneList.append(lw, nw);
    more.hidden = phoneRows.length >= INS_PHONES;
    return r;
  }
  for (let i = 0; i < Math.min(INS_PHONES, have.length + 1); i++) addPhone(have[i]);
  const years = new Map(rec.years.map(y => [y.start, y.end]));
  (plan?.years || []).forEach(y => years.has(y) || years.set(y, ""));
  const payers = new Map(rec.payers.map(x => [x.name, x.claims]));
  (plan?.payers || []).forEach(x => payers.has(x) || payers.set(x, 0));
  const tick = (attrs, text, on) => el("label", {class: "check"}, el("input", {type: "checkbox", ...attrs, checked: on || null}), text);
  const linkSet = years.size || payers.size ? box("Linked to",
    years.size ? el("p", {class: "hint", text: "Its plan years, as typed on Costs:"}) : null,
    ...[...years].sort().map(([s, e]) => tick({"data-year": s}, e ? `${fmtD(s)} to ${fmtD(e)}` : `The plan year from ${fmtD(s)}`, plan?.years?.includes(s))),
    payers.size ? el("p", {class: "hint", text: "Its claims, by the insurer they name:"}) : null,
    ...[...payers].map(([x, n]) => tick({"data-payer": x}, n ? `${x} · ${n} ${n === 1 ? "claim" : "claims"}` : x, plan?.payers?.includes(x)))) : null;
  const cardSet = box("On the emergency card",
    el("p", {class: "hint", text: "It has the plan's name, member ID and group number, while the plan hasn't ended. It can say these too:"}),
    ...Object.entries(INS_CARD).map(([k, t]) => tick({"data-card": k}, t, plan?.card?.includes(k))));
  const fromRec = !plan && rec.plans.length ? box("From your records",
    el("p", {class: "hint", text: "The claims imported here name these plans. Pick one to see what it suggests for each field."}),
    el("div", {class: "row"}, ...rec.plans.map(s => el("button", {type: "button", class: "btn small",
      text: [s.name, s.member && `Member ID ${s.member}`, `${s.claims} ${s.claims === 1 ? "claim" : "claims"}`].filter(Boolean).join(" · "), onclick: () => {
        const {claims, last, ...found} = s, n = offer(found, "From your records");
        sayIn(said, n ? `${s.name} suggests ${n} ${n === 1 ? "field" : "fields"}, each marked below with Use.` : "Every field already says what it suggests.");
      }})))) : null;
  const yes = await ask({title: plan ? `Change ${plan.name}` : "Add an insurance plan", action: plan ? "Save" : "Add plan", field: F.name, cls: "ins-dlg", message: [
    fromRec,
    box("Photos of the card", el("p", {class: "hint", text: "The card is found in the photo, straightened and cropped; only that is saved."}),
      blocks.front, blocks.back,
      readBtn ? el("div", {class: "row"}, readBtn) : el("p", {class: "hint", text: "Text recognition isn't on for this server, so the card can't be read here."})),
    said, useAll,
    name, choose("kind", "Plan type (optional)", plan?.kind, INS_KIND), choose("order", "Primary or secondary (optional)", plan?.order, INS_ORDER), ...ids,
    box("Policyholder", field("holder", "Policyholder's name (optional)", plan?.holder, {maxlength: 120}),
      choose("relation", "Relationship to the policyholder (optional)", plan?.relation, INS_REL)),
    box("Dates", field("start", "Starts (optional)", plan?.start, {type: "date"}), field("end", "Ends (optional)", plan?.end, {type: "date"}),
      el("p", {class: "hint", text: "Once its end date has passed, the plan is listed under Past."})),
    box("Pharmacy and payer", ...INS_RX.map(([k, t]) => field(k, `${t} (optional)`, plan?.[k], {maxlength: 60})),
      field("website", "Plan's website (optional)", plan?.website, {maxlength: 200, inputmode: "url", placeholder: "www.example.com"})),
    box("Phone numbers", phoneList, el("div", {class: "row"}, more)),
    linkSet, cardSet,
    el("p", {class: "hint", text: "As the card prints them."})]});
  const ticked = sel => linkSet ? [...linkSet.querySelectorAll(`input[${sel}]:checked`)].map(b => b.getAttribute(sel)) : [];
  const out = !yes ? null : {fields: {name: F.name.value, member: F.member.value, group: F.group.value,
    phones: phoneRows.map(r => ({label: r.l.value, number: r.num.value})).filter(x => x.label.trim() || x.number.trim()),
    ...Object.fromEntries(["kind", "order", "holder", "relation", "start", "end", "rxbin", "rxpcn", "rxgroup", "payer", "website"].map(k => [k, F[k].value])),
    years: ticked("data-year"), payers: ticked("data-payer"),
    card: [...cardSet.querySelectorAll("input[data-card]:checked")].map(b => b.dataset.card)}, sides};
  return out;
}

async function drawInsurance(p, focus) {
  INS_P = p;
  const card = $("#insurance"), body = $("#insuranceBody"), msg = $("#insuranceMsg");
  card.hidden = false;
  let d;
  try { d = await insCall(`profiles/${p.id}/insurance`); }
  catch (e) { body.replaceChildren(); sayIn(msg, e.message, true); return; }
  if (INS_P !== p) return;
  const edit = d.can_change, again = f => drawInsurance(p, f);
  const act = async (run, said, f) => {   // a change, said in the card's line (said(its reply), or words), the card drawn again with focus back near it
    try { const j = await run(); sayIn(msg, typeof said === "function" ? said(j) : said); } catch (e) { sayIn(msg, e.message, true); }
    await again(f);
  };
  const btn = (text, label, run, extra = {}) => el("button", {class: "btn small", type: "button", text, "aria-label": label, onclick: run, ...extra});
  /* the dialog, then the plan saved and each side's photo sent (or taken off) after it */
  const open = async (c, f) => {
    const rec = await insCall(`profiles/${p.id}/insurance/suggestions`).catch(() => null);
    const got = await insForm(p, c, {canRead: d.can_read, rec});
    if (!got) return;
    act(async () => {
      const j = await insCall(`profiles/${p.id}/insurance${c ? "/" + c.id : ""}`, c ? "PUT" : "POST", got.fields), iid = c ? c.id : j.id, removed = [];
      let photos = 0;
      for (const s of ["front", "back"]) {
        const st = got.sides[s];
        if (st.blob) {
          const r = await insCall(`profiles/${p.id}/insurance/${iid}/${s}`, "PUT", st.blob, {"Content-Type": st.blob.type || "image/jpeg"});
          removed.push(...r.removed || []); photos++;
        } else if (st.drop && st.saved) await insCall(`profiles/${p.id}/insurance/${iid}/${s}`, "DELETE");
      }
      return {photos, removed: [...new Set(removed)], any: photos || ["front", "back"].some(s => got.sides[s].saved && !got.sides[s].drop)};
    }, j => `${c ? "Saved" : "Added"} ${got.fields.name.trim()}.` + (j.photos ? ` Saved ${j.photos === 1 ? "its photo" : "both photos"}.` : "")
      + (j.removed.length ? ` Taken out of the file before it was kept: ${j.removed.join(", ")}.` : "")
      + (!j.any ? " Add a photo of each side of its card with Change." : ""), f);
  };
  const row = (c, i, past) => {
    const hd = "ins" + c.id, url = s => `api/profiles/${p.id}/insurance/${c.id}/${s}?v=${c.changed || c.at}`;
    const line = parts => { const t = parts.filter(Boolean).join(" · "); return t ? el("p", {text: t}) : null; };
    const pics = ["front", "back"].filter(s => c[s]).map(s => el("figure", {class: "jpic"},
      el("a", {href: url(s), target: "_blank", rel: "noopener"}, el("img", {src: url(s), alt: `${INS_SIDE[s]} of the card for ${c.name}`})),
      el("figcaption", {text: INS_SIDE[s]})));
    const dates = c.start && c.end ? `From ${fmtD(c.start)} to ${fmtD(c.end)}` : c.start ? `From ${fmtD(c.start)}` : c.end ? `${past ? "Ended" : "Ends"} ${fmtD(c.end)}` : "";
    const links = [c.years?.length && `Plan years from ${c.years.map(fmtD).join(", ")}`, c.payers?.length && `claims from ${c.payers.join(", ")}`].filter(Boolean);
    const acts = !edit ? (past ? null : el("div", {class: "acts"}, btn("Show at the desk", `Show the card for ${c.name} at the desk`, () => insDesk(p.id, c.id))))
      : el("div", {class: "acts", onclick: () => { INS_AT = i; }},
        past ? null : btn("Show at the desk", `Show the card for ${c.name} at the desk`, () => insDesk(p.id, c.id)),
        btn("Change…", `Change the plan ${c.name}`, () => open(c, c.id)),
        btn("Remove", `Remove the plan ${c.name}`, async () => {
          if (!await ask({title: `Remove ${c.name}?`, message: "Its member ID, numbers and card photos are deleted. This can't be undone.", action: "Remove plan", danger: true})) return;
          act(() => insCall(`profiles/${p.id}/insurance/${c.id}`, "DELETE"), `Removed ${c.name}.`, c.id);
        }));
    return setRow([el(past ? "h4" : "h3", {class: "sub", id: hd}, c.name),
      line([insKind(c), c.member && `Member ID ${c.member}`, c.group && `Group ${c.group}`]),
      line(INS_RX.map(([k, t]) => c[k] && `${t} ${c[k]}`)),
      c.holder ? el("p", {text: `Policyholder: ${c.holder}` + (c.relation ? ` (${INS_REL[c.relation]})` : "")}) : null,
      dates ? el("p", {class: "muted", text: dates}) : null,
      c.website ? el("p", {}, el("a", {href: c.website, target: "_blank", rel: "noopener noreferrer", text: c.website.replace(/^https?:\/\//, "")})) : null,
      ...c.phones.map(ph => el("p", {class: "muted"}, ph.label ? ph.label + ": " : "", el("a", {href: "tel:" + ph.number.replace(/[^0-9+*#,]/g, ""), text: ph.number}))),
      links.length ? el("p", {class: "muted", text: "Linked to " + links.join("; ")}) : null,
      pics.length ? el("div", {class: "jpics"}, ...pics) : el("p", {class: "muted", text: "No photo of the card."})],
      acts, {tag: "section", "aria-labelledby": hd, "data-plan": c.id});
  };
  const add = edit ? btn("Add a plan…", "Add an insurance plan", () => open(null, "insAdd"), {id: "insAdd"}) : null;
  const now = d.plans.filter(c => !insPast(c)), past = d.plans.filter(insPast);
  const rows = el("div", {}, ...now.map((c, i) => row(c, i)),
    past.length ? [el("h3", {class: "sub ins-past", id: "insPast", text: "Past"}), ...past.map((c, i) => row(c, now.length + i, true))] : null);
  body.replaceChildren(
    d.plans.length ? rows : emptyState("No insurance plan yet." + (edit ? " Add one with what its card says, and a photo of each side." : ""), null, {cls: ""}),
    add && d.plans.length < 10 ? el("div", {class: "row"}, add) : null);
  if (focus === "insAdd" && $("#insAdd")) $("#insAdd").focus();
  else if (typeof focus === "string" && document.getElementById(focus)) document.getElementById(focus).focus();
  else if (focus != null) {
    const all = [...rows.querySelectorAll("[data-plan]")], at = all.findIndex(r => r.dataset.plan === String(focus));
    focusNear(all, at < 0 ? Math.min(INS_AT, all.length) : at, "button", $("#insAdd") || $("#insuranceHd"));
  }
}
