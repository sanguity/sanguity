"""Menstrual cycle tracking in the app (#183): the setting that turns it on for a profile, what the watch receiver is
told, and what the page reads. server/cycle.py has the records themselves and the privacy rules; server/routes_cycle.py
the routes.

The setting is a settings-table row, key "_cycle" (profile scope, sealed with crypto.enc like any private setting, so
it goes with the profile when it's deleted; not in the settings registry, so it's never in the merged settings, an
export or a reset): {"on", "since", "changed", "by", "purge", "purge_at"}. Off is the default: no row. Only the
profile's owners change it; its owners and editors see the records; viewers neither, and the page doesn't tell them
it exists.

The app never keeps the records. The receiver keeps them (in its sealed database and archive) for a profile it's told
has tracking on, and writes them to cycle.json beside the profile's daily.json; the app reads that file when a page
asks (read()). watch_keys.publish() tells the receiver, in its secrets file, which profiles' slots have tracking on
("cycle") and the latest deletion each asked for ("cycle_purge": a new random token each time). Turning tracking
off asks for a deletion too. The receiver deletes the records from its database and rewrites every archived upload
without them, then says which deletion it did in cycle.json ("purged"), which status() shows. Until then nothing is
shown: a deletion asked for hides the records at once, whatever the receiver has done.
"""
import json, os, secrets

from . import crypto, cycle, db as dbm

KEY = "_cycle"


def setting(db, pid):
    r = db.execute("SELECT sealed FROM settings WHERE key=? AND profile_id=? AND user_id IS NULL", (KEY, pid)).fetchone()
    if r is None or r["sealed"] is None:
        return {}
    try:
        v = json.loads(crypto.dec(r["sealed"]))
    except (ValueError, TypeError):
        return {}
    return v if isinstance(v, dict) else {}


def is_on(db, pid):
    return bool(setting(db, pid).get("on"))


def _store(db, pid, v, by):
    db.execute("""INSERT INTO settings(key, profile_id, user_id, value, sealed, changed, changed_by) VALUES(?, ?, NULL, NULL, ?, ?, ?)
                  ON CONFLICT(key, IFNULL(profile_id, 0), IFNULL(user_id, 0))
                  DO UPDATE SET sealed=excluded.sealed, changed=excluded.changed, changed_by=excluded.changed_by""",
               (KEY, pid, crypto.enc(json.dumps(v, separators=(",", ":"))), dbm.now(), by))


def turn(db, pid, on, by):
    """Turn tracking on or off (inside the caller's transaction; publish after it commits). Off asks the receiver to
    delete everything it keeps. Returns whether anything changed."""
    v = setting(db, pid)
    if bool(v.get("on")) == bool(on):
        return False
    v.update(on=bool(on), changed=dbm.now(), by=by)
    if on:
        v["since"] = dbm.now()
    else:
        v.pop("since", None)
        v.update(purge=secrets.token_hex(12), purge_at=dbm.now())
    _store(db, pid, v, by)
    return True


def delete_all(db, pid, by):
    """Ask for everything kept to be deleted, leaving tracking as it is (inside the caller's transaction)."""
    v = setting(db, pid)
    v.update(purge=secrets.token_hex(12), purge_at=dbm.now())
    _store(db, pid, v, by)


def keys_doc(db):
    """What the receiver's secrets file says about cycle tracking: {"cycle": [slots with it on], "cycle_purge":
    {slot: the latest deletion asked for}}."""
    from . import watch_keys
    on, purge = [], {}
    for r in db.execute("SELECT s.profile_id AS pid FROM settings s JOIN profiles p ON p.id=s.profile_id "
                        "WHERE s.key=? AND s.user_id IS NULL ORDER BY s.profile_id", (KEY,)).fetchall():
        v, slot = setting(db, r["pid"]), watch_keys._slot_for(db, r["pid"])
        if v.get("on"):
            on.append(slot)
        if v.get("purge"):
            purge[slot] = v["purge"]
    return {"cycle": on, "cycle_purge": purge}


def path(db, pid):
    """Where the receiver writes this profile's cycle.json: beside its daily.json."""
    from . import watch_keys
    return os.path.join(os.path.dirname(watch_keys.summary_path(watch_keys._slot_for(db, pid))), "cycle.json")


def read(db, pid):
    p = path(db, pid)
    try:
        v = json.loads(crypto.read_file(p)) if os.path.isfile(p) else None
    except (OSError, ValueError, crypto.KeyError_):
        return None
    return v if isinstance(v, dict) else None


def status(db, pid, role):
    """What the page is given (owners and editors only): whether tracking is on and who may change it, the latest
    deletion and whether the receiver has done it, and, while on, the spans and marks to show."""
    from . import watch_keys
    v, doc = setting(db, pid), read(db, pid) or {}
    on, token = bool(v.get("on")), v.get("purge")
    pending = bool(token) and doc.get("purged") != token
    out = {"on": on, "can_change": role == "owner", "since": v.get("since") if on else None, "ready": watch_keys.ready(),
           "deletion": {"asked": v.get("purge_at"), "done": None if pending else doc.get("purged_at")} if token else None,
           "spans": [], "marks": [], "count": 0, "updated": None}
    if on and not pending and doc.get("on"):
        recs = doc.get("records") if isinstance(doc.get("records"), list) else []
        v = cycle.view(recs)
        out.update(spans=[{**s, **dict(zip(("title", "text"), cycle.words(s)))} for s in v["spans"]],
                   marks=[{**m, "title": cycle.mark_words(m)} for m in v["marks"]], count=len(recs), updated=doc.get("updated"))
    return out


def summary_rows(db, pid, data, since, now):
    """The visit summary's "Cycle tracking" section (offered to owners and editors while tracking is on, unticked
    every time: routes_summary.py): the periods and other records since the "since" day, as recorded."""
    st = status(db, pid, "editor")
    if not st["on"] or (st["deletion"] and not st["deletion"]["done"]):
        return []
    rows = [{"when": s["from"], "title": s["title"], "text": s["text"]} for s in st["spans"] if s["to"] > since]
    rows += [{"when": m["day"], "title": m["title"], "text": m.get("app") or ""} for m in st["marks"] if m["day"] > since]
    return sorted(rows, key=lambda r: r["when"], reverse=True)


SUMMARY_SECTION = ("cycle", "Cycle tracking (as recorded by the phone apps)")


def for_export(db, pid):
    """The records an export carries when its cycle box is ticked: None while tracking is off (or a deletion is
    waiting), else {"spans", "marks"} in the page's shape, with what each one says in words."""
    st = status(db, pid, "editor")
    if not st["on"] or (st["deletion"] and not st["deletion"]["done"]):
        return None
    return {"note": "Menstrual cycle records from the phone apps, as recorded: Sanguity doesn't predict or interpret them.",
            "spans": st["spans"], "marks": st["marks"]}
