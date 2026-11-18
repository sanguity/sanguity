/* Sanguity · a record's details, loaded when it's opened (#217).
   The records (GET api/profiles/{pid}/data) come with only what the page shows of details without opening anything:
   a lab day's list of results whole (DATA.details[id].ids: how many, search, the drawer's list), and of an
   immunization what the Overview lists, marked "partial". The rest (a result's rows, a claim's lines, a message, a
   letter, a document's address) is GET api/profiles/{pid}/details?ids=... when the drawer opens that record (a lab
   day: its results, in one request), kept in DATA.details for as long as the page is open, in this person's units
   (units.js). Offline (offline.js), what was opened on this device while online is answered from the offline copy.
   Plain script, loaded in order by index.html before drawer.js; all files share one global scope. */
"use strict";
const DETAILS_GOT = new Set();      // ids asked for and answered, with details or none
const DETAILS_MISSED = new Map();   // id -> why its details couldn't be loaded, as the drawer says it once
const DETAILS_AT_ONCE = 500;        // server/records.py DETAILS_MAX
const detailsHave = id => DETAILS_GOT.has(id) || !!(DATA.details[id] && !DATA.details[id].partial);

/* load the details of these records that aren't in yet; never throws (what couldn't be loaded is in DETAILS_MISSED) */
async function loadDetails(ids) {
  const want = [...new Set(ids)].filter(id => id != null && !detailsHave(id));
  for (let i = 0; i < want.length; i += DETAILS_AT_ONCE) {
    const part = want.slice(i, i + DETAILS_AT_ONCE);
    let got = null, why = "";
    try {
      const r = await api(`profiles/${PROFILE.id}/details?ids=${part.join(",")}`);
      if (r.status === 401) location.replace("login.html");   // the sign-in ended
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.details) got = j;
      else why = typeof j.detail === "string" ? j.detail : `The server answered ${r.status}.`;
    } catch (err) { why = "Couldn't reach the server: " + err.message; }
    if (!got) { for (const id of part) DETAILS_MISSED.set(id, `Its details couldn't be loaded. ${why}`); continue; }
    const notKept = new Set(got.missing || []), fresh = {};   // missing: offline, not opened on this device before
    for (const id of part) {
      if (notKept.has(id)) { DETAILS_MISSED.set(id, "Its details weren't kept on this device: only records opened while online are."); continue; }
      if (got.details[id]) DATA.details[id] = fresh[id] = got.details[id];
      DETAILS_GOT.add(id);
    }
    applyUnitsToRows(DATA.labs, fresh);   // this person's units, as for everything else (#41)
  }
}

/* the drawer opening e (drawer.js): while its details (a lab day's: its results') are loaded, the drawer says so and
   this is the promise of e opened again once they're in (so openEvent's own, too); after that, null, and a note at
   the top when some couldn't be loaded */
function detailsWait(e, body) {
  const d = DATA.details[e.id], ids = [e.id, ...(d && d.type === "labday" && Array.isArray(d.ids) ? d.ids : [])];
  const want = ids.filter(id => !detailsHave(id) && !DETAILS_MISSED.has(id));
  if (want.length) {
    body.append(el("p", {class: "hint details-wait", role: "status", text: "Loading its details…"}));
    return loadDetails(want).then(() => { if (state.selEv === e.id && drawer.classList.contains("open")) return openEvent(e); });
  }
  const why = ids.map(id => DETAILS_MISSED.get(id)).find(x => x != null);
  if (why != null) body.append(el("p", {class: "hint details-missed", role: "status", text: why}));
  for (const id of ids) DETAILS_MISSED.delete(id);   // asked for again the next time it's opened
  return null;
}
