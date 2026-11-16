"""Milestones in the app (#39): moving the old ones into the "milestones" setting (server/settings.py).

Before #39 a profile's milestones were part of its records: corrections.json's "milestones" (or its older single
"diagnosis_date") went into the setup build's data.json, and every data.json import (an export, the demo) kept its
own in the import's meta. The page read them from there. Now they are a profile setting, changed on the Settings
page, and the page reads them from the settings it loads.

  migrate()   once, at start-up: each profile without milestones set gets those of its imports' meta, and a
              profile linked to the setup build (else the first profile) also corrections.json's. After that
              corrections.json is not needed (build.py no longer reads it).

Later imports leave the setting alone (so undoing one restores exactly what was there), with one exception: an
export from before #39 loaded back in whole (profiles.import_dataset, which replaces the profile's settings with
the export's settings.json) brings the milestones its data.json holds when its settings.json has none.

A milestone the setting refuses (no date, a name too long) is left out, with a line in the log; when more than one
is marked primary, the earliest stays primary.
"""
import json

from . import crypto, db as dbm, settings

KEY = "milestones"
MIGRATED = "milestones_migrated"   # meta: when the milestones of imports and corrections.json were moved in


def old(obj):
    """The milestones in a data.json's meta or corrections.json (the older single date as one "Diagnosis")."""
    if not isinstance(obj, dict):
        return []
    ms = [m for m in obj.get("milestones") or [] if isinstance(m, dict) and m.get("date")] \
        if isinstance(obj.get("milestones"), list) else []
    if not ms:
        d = obj.get("diagnosis_date") or obj.get("dxDate")
        if isinstance(d, str) and d:
            ms = [{"date": d, "label": "Diagnosis"}]
    return ms


def clean(items, where):
    """items, each checked on its own (a bad one left out, said in the log), with at most one primary."""
    check = settings.SETTINGS[KEY].clean
    out = []
    for m in items:
        try:
            out += check([{k: m.get(k) for k in ("date", "label", "primary") if m.get(k) is not None}])
        except settings.SettingError as e:
            print(f"{where}: a milestone was left out ({e})", flush=True)
    out.sort(key=lambda m: (m["date"], m["label"]))
    seen = False
    for m in out:
        if m.get("primary"):
            if seen:
                del m["primary"]
            seen = True
    return check(out)


def _has(db, pid):
    return KEY in settings.stored(db, "profile", pid)


def migrate(db):
    """Once, at start-up (after the first profile exists). Returns the number of profiles given milestones, or None
    when it had already run or has to wait (corrections.json there, but no profile yet)."""
    from . import edits, records
    if dbm.meta_get(db, MIGRATED):
        return None
    pids = [r["id"] for r in db.execute("SELECT id FROM profiles ORDER BY id")]
    path = edits.corrections_file()
    if path and not pids:
        return None
    corr = []
    if path:
        try:
            corr = old(json.loads(crypto.read_file(path).decode("utf-8")))
        except (OSError, ValueError) as e:
            print(f"corrections.json can't be read ({type(e).__name__}: {e}); its milestones weren't moved into the "
                  "app. Add them on the Settings page.", flush=True)
    linked = [r["id"] for r in db.execute("SELECT id FROM profiles WHERE linked_build=1 ORDER BY id")] or pids[:1]
    n = 0
    with dbm.transaction(db):
        for pid in pids:
            if _has(db, pid):
                continue
            ms = old(records.metas(db, pid)) + (corr if pid in linked else [])
            ms = clean(ms, f"profile {pid}'s milestones") if ms else []
            if ms:
                settings.put(db, KEY, ms, "profile", pid)
                n += 1
        dbm.meta_set(db, MIGRATED, str(dbm.now()))
    return n
