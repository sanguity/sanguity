"""Key documents (#1135): a document in the records marked as an advance directive, a power of attorney, a POLST and
the like, so it's one tap away (the Overview's Key documents, the palette) and the emergency card can say it's on file.

A mark is the profile's: owners and editors make, change and remove it in the document's drawer, with an optional
"kept at" note ("original in the hall desk; copy with Dr Lee"). The marks are one profile setting, keyDocs
(settings.py, sealed, not on the Settings screen): {event key: {"kind", "kept"}}, the key the event's edits use
(edits.key_of: its source key, else a content key), so a mark finds its document again after a re-import, and goes
with the profile's settings into the export and the backups. A mark whose document is gone stays stored, unapplied,
until it comes back. Changing it moves the records' revision (Setting.records), since the records carry it:
annotate() gives each marked event "keyDoc": {"kind", "kept"} as the records are put together (records._assemble).

The emergency card (emergency.py) says each "<kind>: on file (kept at ...)", never the document itself; the
lock-screen image doesn't.
"""
from . import edits

KINDS = {"advance_directive": "Advance directive or living will", "poa": "Healthcare power of attorney",
         "polst": "POLST or MOLST", "dnr": "DNR order", "organ": "Organ donation", "other": "Other key document"}
SAID = {**KINDS, "other": "Key document"}   # as the card says it
DOC_KINDS = ("document", "letter", "imaging", "study", "procedure", "visit", "contact", "message")   # what can hold a file
MOST, KEY_MOST, KEPT_MOST = 100, 400, 200


class KeyDocError(ValueError):
    """A mark that can't be made, said in plain language."""


def check(v):
    """The setting's value, cleaned: {key: {"kind", "kept"}}."""
    if not isinstance(v, dict):
        raise KeyDocError("Key documents must be a list of marks.")
    if len(v) > MOST:
        raise KeyDocError(f"At most {MOST} key documents.")
    out = {}
    for k, m in v.items():
        if not isinstance(k, str) or not k.strip() or len(k) > KEY_MOST or not isinstance(m, dict):
            raise KeyDocError("Each key document names its record.")
        if m.get("kind") not in KINDS:
            raise KeyDocError("A key document is an advance directive, a power of attorney, a POLST, a DNR order, "
                              "organ donation, or another key document.")
        kept = m.get("kept") or ""
        if not isinstance(kept, str):
            raise KeyDocError("Where it's kept is text.")
        kept = " ".join(kept.split())
        if len(kept) > KEPT_MOST:
            raise KeyDocError(f"Where it's kept is at most {KEPT_MOST} characters.")
        out[k] = {"kind": m["kind"], "kept": kept}
    return out


def marks(db, pid):
    from . import settings
    return settings.stored(db, "profile", pid).get("keyDocs") or {}


def annotate(db, pid, data):
    """Each marked event gets "keyDoc": {"kind", "kept"} (called with the events as imported, edits made)."""
    have = marks(db, pid)
    if not have:
        return
    for e in data.get("events") or []:
        if isinstance(e, dict) and e.get("k") in DOC_KINDS:
            m = next((have[k] for k in edits.keys_of(e) if k in have), None)
            if m:
                e["keyDoc"] = dict(m)


def on_card(data):
    """The emergency card's Key documents: [{"kind", "kept", "line"}], one line per kind, in KINDS' order."""
    seen = {}
    for e in (data or {}).get("events") or []:
        m = isinstance(e, dict) and not e.get("hidden") and e.get("keyDoc")
        if m and m["kind"] not in seen:
            seen[m["kind"]] = m
        elif m and m["kept"] and not seen[m["kind"]]["kept"]:
            seen[m["kind"]] = m
    return [{"kind": k, "kept": m["kept"], "line": f"{SAID[k]}: on file" + (f" (kept at {m['kept']})" if m["kept"] else "")}
            for k in KINDS if (m := seen.get(k))]
