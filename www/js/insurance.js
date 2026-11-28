/* Sanguity · insurance cards (#1137): Settings → This person → Insurance cards, the card #insurance. Each health plan
   of the profile with what its card says (the plan's name, member ID, group number, phone numbers) and a photo of each
   side, taken with the phone's camera or chosen from a file. server/insurance.py keeps them (routes_insurance.py):
   owners and editors add, change and remove them, everyone the profile is shared with sees them. A share link shows
   them only when its maker ticks Insurance cards; Export everything has them. drawInsurance(p): the card, drawn for
   the profile shown (settings.js). Plain script; all files share one scope. */
"use strict";
const INS_TYPES = ["image/jpeg", "image/png", "image/webp"], INS_PHONES = 4;
const INS_SIDE = {front: "Front", back: "Back"};
let INS_P = null, INS_AT = 0;   // the profile drawn; where the row acted on was
async function insCall(path, method = "GET", body, headers) {
  const r = await api(path, {method, body: body === undefined || body instanceof Blob ? body : JSON.stringify(body), headers});
  if (r.status === 401) { location.replace("login.html"); throw new Error("signed out"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `${r.status} ${r.statusText}`);
  return j;
}
/* the form, in the app's dialog: a plan's fields (plan: the one changed, or null for a new one) -> its fields, or null */
async function insForm(title, plan, action) {
  const id = "insF" + (insForm.n = (insForm.n || 0) + 1);
  const field = (name, label, value, attrs = {}) => {
    const input = el("input", {type: "text", id: `${id}-${name}`, value: value || "", autocomplete: "off", spellcheck: "false", ...attrs});
    return [el("label", {for: input.id, text: label}), input];
  };
  const [nameL, name] = field("name", "Plan's name", plan?.name, {maxlength: 120, required: true});
  const [memberL, member] = field("member", "Member ID (optional)", plan?.member, {maxlength: 60});
  const [groupL, group] = field("group", "Group number (optional)", plan?.group, {maxlength: 60});
  const have = plan?.phones || [], rows = Math.min(INS_PHONES, have.length + 1);
  const phones = Array.from({length: rows}, (_, i) => {
    const n = i + 1, p = have[i] || {};
    const [lL, l] = field(`pl${n}`, `Phone ${n}: what it's for (optional)`, p.label, {maxlength: 40, placeholder: "e.g. Member services"});
    const [nL, num] = field(`pn${n}`, `Phone ${n}: number (optional)`, p.number, {maxlength: 40, type: "tel", inputmode: "tel"});
    return {nodes: [lL, l, nL, num], l, num};
  });
  const yes = await ask({title, action, field: name, message: [nameL, name, memberL, member, groupL, group, ...phones.flatMap(p => p.nodes),
    el("p", {class: "hint", text: "As the card prints them. Add a photo of each side once it's saved."})]});
  if (!yes) return null;
  return {name: name.value, member: member.value, group: group.value,
    phones: phones.map(p => ({label: p.l.value, number: p.num.value})).filter(p => p.label.trim() || p.number.trim())};
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
  const row = (c, i) => {
    const hd = "ins" + c.id, url = s => `api/profiles/${p.id}/insurance/${c.id}/${s}?v=${c.changed || c.at}`;
    const numbers = [c.member && `Member ID ${c.member}`, c.group && `Group ${c.group}`].filter(Boolean).join(" · ");
    const pics = ["front", "back"].filter(s => c[s]).map(s => el("figure", {class: "jpic"},
      el("a", {href: url(s), target: "_blank", rel: "noopener"}, el("img", {src: url(s), alt: `${INS_SIDE[s]} of the card for ${c.name}`})),
      el("figcaption", {text: INS_SIDE[s]})));
    const picker = s => {   // a side's photo: the phone offers its camera, or a file
      const input = el("input", {type: "file", accept: INS_TYPES.join(","), id: `${hd}-${s}`, "aria-label": `${c[s] ? "Replace the" : "Add a"} photo of the ${s} of the card for ${c.name}`});
      input.onchange = () => {
        const f = input.files[0]; if (!f) return;
        if (f.type && !INS_TYPES.includes(f.type)) { sayIn(msg, `${f.name} isn't a JPEG, PNG or WebP picture.`, true); input.value = ""; return; }
        sayIn(msg, `Sending ${f.name}…`);
        INS_AT = i;
        act(() => insCall(`profiles/${p.id}/insurance/${c.id}/${s}`, "PUT", f, {"Content-Type": f.type || "application/octet-stream"}),
          j => `Saved the photo of the ${s} of the card for ${c.name}.` + (j.removed?.length ? ` Taken out of the file before it was kept: ${j.removed.join(", ")}.` : ""),
          `${hd}-${s}`);
      };
      return el("label", {class: "btn small pick"}, `${c[s] ? "Replace" : "Add"} ${s} photo…`, input);
    };
    const acts = !edit ? null : el("div", {class: "acts", onclick: () => { INS_AT = i; }},
      btn("Change…", `Change the plan ${c.name}`, async () => {
        const got = await insForm(`Change ${c.name}`, c, "Save");
        if (got) act(() => insCall(`profiles/${p.id}/insurance/${c.id}`, "PUT", got), `Saved ${got.name.trim()}.`, c.id);
      }),
      picker("front"), picker("back"),
      ...["front", "back"].filter(s => c[s]).map(s => btn(`Take off ${s} photo`, `Take off the photo of the ${s} of the card for ${c.name}`,
        () => act(() => insCall(`profiles/${p.id}/insurance/${c.id}/${s}`, "DELETE"), `Took off the photo of the ${s} of the card for ${c.name}.`, c.id))),
      btn("Remove", `Remove the plan ${c.name}`, async () => {
        if (!await ask({title: `Remove ${c.name}?`, message: "Its member ID, numbers and card photos are deleted. This can't be undone.", action: "Remove plan", danger: true})) return;
        act(() => insCall(`profiles/${p.id}/insurance/${c.id}`, "DELETE"), `Removed ${c.name}.`, c.id);
      }));
    return setRow([el("h3", {class: "sub", id: hd}, c.name),
      numbers ? el("p", {text: numbers}) : null,
      ...c.phones.map(ph => el("p", {class: "muted"}, ph.label ? ph.label + ": " : "", el("a", {href: "tel:" + ph.number.replace(/[^0-9+*#,]/g, ""), text: ph.number}))),
      pics.length ? el("div", {class: "jpics"}, ...pics) : el("p", {class: "muted", text: "No photo of the card."})],
      acts, {tag: "section", "aria-labelledby": hd, "data-plan": c.id});
  };
  const add = edit ? btn("Add a plan…", "Add an insurance plan", async () => {
    const got = await insForm("Add an insurance plan", null, "Add plan");
    if (got) act(() => insCall(`profiles/${p.id}/insurance`, "POST", got), `Added ${got.name.trim()}. Add a photo of each side of its card.`, "insAdd");
  }, {id: "insAdd"}) : null;
  const rows = el("div", {}, ...d.plans.map(row));
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
