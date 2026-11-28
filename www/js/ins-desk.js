/* Sanguity · an insurance card at the desk (#1168): the card full screen, for a phone held out at a front desk. Its
   front, then the back by tapping the card, swiping it, or Front and Back; the member ID and group number large as
   text; each plan that hasn't ended, chosen at the top. The screen is kept on while it's open where the browser can
   (the Screen Wake Lock API), and that's said. Opened from a plan's Show at the desk (insurance.js) and from the
   command palette on every page (palette.js); on the records page offline it's drawn from the copy this device keeps
   (offline.js keeps the plans and their photos with the records). Its photos are fetched, not linked, so the copy
   answers them too. insDesk(pid, first): first, the plan shown first. Plain script; all files share one scope. */
"use strict";
async function insDesk(pid, first) {
  const photo = {};   // {"id/side": its data: address}
  const dlg = el("dialog", {class: "ins-desk", "aria-labelledby": "dkTitle"});
  const close = el("button", {type: "button", class: "btn", text: "Close", onclick: () => dlg.close()});
  const title = el("h2", {id: "dkTitle", text: "Insurance card"});
  const main = el("div", {class: "dk-main"}, el("p", {class: "muted", text: "Loading…"}));
  const note = el("p", {class: "hint dk-lock", "aria-live": "polite"});
  dlg.append(el("div", {class: "dk-top"}, title, close), main, note);
  document.body.append(dlg);
  let lock = null;
  const keepOn = async () => {
    if (!("wakeLock" in navigator) || document.visibilityState !== "visible" || !dlg.open) return;
    try {
      lock = await navigator.wakeLock.request("screen");
      note.textContent = "The screen stays on while this is open.";
      lock.addEventListener("release", () => { if (dlg.open) note.textContent = ""; });
    } catch (e) { note.textContent = ""; }
  };
  const seen = () => { if (document.visibilityState === "visible" && dlg.open && (!lock || lock.released)) keepOn(); };
  document.addEventListener("visibilitychange", seen);
  dlg.addEventListener("close", () => {
    document.removeEventListener("visibilitychange", seen);
    lock?.release?.().catch(() => {});
    dlg.remove();
  });
  openDialog(dlg);
  close.focus();
  keepOn();
  let plans;
  try {
    const r = await api(`profiles/${pid}/insurance`), j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `${r.status} ${r.statusText}`);
    plans = (j.plans || []).filter(c => !(c.end && c.end < zToday()));
  } catch (e) { main.replaceChildren(el("p", {class: "msg bad", text: e.message})); return; }
  if (!plans.length) { main.replaceChildren(el("p", {text: "No insurance plan to show. Add one under Settings, Insurance cards."})); return; }
  const pic = async (c, s) => {
    const k = `${c.id}/${s}`;
    if (!(k in photo)) {
      photo[k] = null;
      try {
        const r = await api(`profiles/${pid}/insurance/${c.id}/${s}?v=${c.changed || c.at}`);
        if (r.ok) photo[k] = await dataUrl(await r.blob());
      } catch (e) { /* not there (offline, not kept): the numbers still are */ }
    }
    return photo[k];
  };
  let plan = plans.find(c => c.id === first) || plans[0], side = "front", swiped = false, x0 = null;
  const flip = () => { if (plan.front && plan.back) { side = side === "front" ? "back" : "front"; draw(true); } };
  async function draw(said) {
    const c = plan, sides = ["front", "back"].filter(s => c[s]);
    if (!sides.includes(side)) side = sides[0] || "front";
    title.textContent = c.name;
    const src = c[side] ? await pic(c, side) : null;
    if (c !== plan) return;
    const alt = `${side === "front" ? "Front" : "Back"} of the card for ${c.name}`;
    const card = src ? el("button", {type: "button", class: "dk-card", "aria-describedby": sides.length > 1 ? "dkHow" : null, onclick: () => { if (swiped) swiped = false; else flip(); },
      onpointerdown: e => { x0 = e.clientX; }, onpointerup: e => { if (x0 != null && Math.abs(e.clientX - x0) > 40) { swiped = true; flip(); } x0 = null; }},
    el("img", {src, alt})) : null;
    const big = [["Member ID", c.member], ["Group", c.group]].filter(([, v]) => v);
    const rx = [["RxBIN", c.rxbin], ["RxPCN", c.rxpcn], ["RxGroup", c.rxgroup], ["Payer ID", c.payer]].filter(([, v]) => v);
    main.replaceChildren(...present(
      plans.length > 1 ? el("div", {class: "row dk-plans", role: "group", "aria-label": "Plans"}, ...plans.map(x => el("button", {type: "button", class: "btn small",
        text: x.name, "aria-pressed": x === c, onclick: () => { plan = x; side = "front"; draw(); }}))) : null,
      card,
      sides.length > 1 ? el("div", {class: "row", role: "group", "aria-label": "Side of the card"}, ...sides.map(s => el("button", {type: "button", class: "btn small",
        text: s === "front" ? "Front" : "Back", "aria-pressed": s === side, onclick: () => { side = s; draw(true); }}))) : null,
      sides.length > 1 ? el("p", {class: "hint", id: "dkHow", text: "Tap the card or swipe it for the other side."}) : null,
      !src && !sides.length ? el("p", {class: "muted", text: "No photo of this card."}) : null,
      big.length ? el("dl", {class: "dk-ids"}, ...big.map(([k, v]) => el("div", {}, el("dt", {text: k}), el("dd", {text: v})))) : null,
      rx.length ? el("p", {class: "dk-rx", text: rx.map(([k, v]) => `${k} ${v}`).join(" · ")}) : null,
      ...c.phones.map(ph => el("p", {class: "dk-rx"}, ph.label ? ph.label + ": " : "", el("a", {href: "tel:" + ph.number.replace(/[^0-9+*#,]/g, ""), text: ph.number})))));
    if (said) { announce(alt); main.querySelector(".dk-card")?.focus(); }
  }
  draw();
}
