"""A profile's watch and scale summary: what the Health tab reads, from a phone receiver and from imports.

The summary is the shape watch/receiver.py writes as daily.json: {"metrics": [{key, name, unit, group,
pts: [[day, value]]}], "exercise": [{start, end, name, kind, minutes, key?}], ...}. A daily point is identified by
(metric key, day) and a workout by its `key` (when it has one) or else its start time, so a later import that
overlaps an earlier one replaces those days and workouts instead of adding to them.

Where it is kept, in the profile's own folder (so encryption at rest, deletion and backups cover it):
  <profile_dir>/watch/imports/<import id>.json   each Apple Health import's own summary, as it came in (issue #24),
                                                 so undoing that import removes exactly what it brought
  <profile_dir>/watch/imports/legacy.json        what was imported before imports were kept apart (moved there at
                                                 start-up by migrate), and anything saved without an import id
                                                 (server.manage import-apple-health); it can't be undone
  <profile_dir>/watch/daily.json         the imports merged, for a profile with no watch file of its own;
                                         profiles.watch_file is pointed at it (and back to none when nothing is left)
  <profile_dir>/watch/apple-health.json  the imports merged, for a profile that already reads another file (a
                                         receiver's), kept beside it and merged in when read, the receiver winning
  <profile_dir>/watch/ecg/<16 hex>.json.gz  an imported ECG recording's samples (#182, server/ecg.py), one file per
                                         recording by its key (so the same recording in two exports is one file),
                                         read only when its drawer opens; rebuild removes those no import lists any more
  <profile_dir>/watch/cycle/<import id>.json  an Apple Health import's cycle records (#223), only while tracking is
                                         on: never in a summary here (cycle_tracking.py has them)
The merged file is rebuilt (rebuild) from the imports' own files, legacy first and then by import id, a later import
winning per day and workout as it did when it came in; only files whose import still exists count. All are sealed
with the app's key (crypto.write_file).
"""
import json, os, secrets
from datetime import datetime, timezone

from . import crypto, ecg, profiles as prof

SIDE = "apple-health.json"
LEGACY = "legacy.json"   # imported before imports were kept apart, or with no import id: not undoable
UNDOING = ".undoing-"     # a summary set aside by an undo that has not committed yet (forget_import)
# lists of readings one by one, [time, value(s)..., source, app] (the phone's, watch/receiver.py #180 #184; typed in
# at home, app "Typed in", manual_items.py #224): list -> where its app is in a row
READINGS = {"bp_readings": 5, "temp_readings": 3, "glucose_readings": 3, "spo2_readings": 3}


def own_path(pid):
    return os.path.join(prof.profile_dir(pid), "watch", "daily.json")


def side_path(pid):
    return os.path.join(prof.profile_dir(pid), "watch", SIDE)


def imports_dir(pid):
    return os.path.join(prof.profile_dir(pid), "watch", "imports")


def layer_path(pid, import_id=None):
    """Where one import's own summary is kept (import_id None: the legacy layer)."""
    return os.path.join(imports_dir(pid), LEGACY if import_id is None else f"{int(import_id)}.json")


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _read(path):
    if not path or not os.path.isfile(path):
        return None
    v = json.loads(crypto.read_file(path))   # sealed, or a receiver's file from before encryption
    return v if isinstance(v, dict) else None


def _instant(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return str(s)
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def _by_start(x):
    t = _instant(x.get("start"))
    return (1, 0, t) if isinstance(t, str) else (0, t, "")


def merge(low, high):
    """`high` laid over `low`: its points win per (metric key, day), its workouts per key and then per start time.
    Either may be None. Top-level fields (updated, tz, last_payload...) come from `high` where it has them, but for the
    lists of readings (READINGS), which keep both, `high`'s winning per minute, source and app (#224: a reading typed
    in again for its minute replaces the one before)."""
    if not low or not high:
        return dict(high or low) if (high or low) else None
    out = {**low, **high}
    metrics, order = {}, []
    for src in (low, high):
        for m in src.get("metrics") or []:
            k = m.get("key")
            if k not in metrics:
                order.append(k)
                metrics[k] = {**m, "pts": {}}
            else:
                metrics[k].update({f: m[f] for f in ("name", "unit", "group") if f in m})   # high names win
            metrics[k]["pts"].update((d, v) for d, v in m.get("pts") or [])
    out["metrics"] = [{**metrics[k], "pts": [[d, v] for d, v in sorted(metrics[k]["pts"].items())]} for k in order]
    hi = list(high.get("exercise") or [])
    keys = {x["key"] for x in hi if x.get("key")}
    starts = {_instant(x.get("start")) for x in hi}
    lo = [x for x in low.get("exercise") or [] if not (x.get("key") and x["key"] in keys) and _instant(x.get("start")) not in starts]
    out["exercise"] = sorted(lo + hi, key=_by_start)
    if low.get("ecg") or high.get("ecg"):   # ECGs and irregular rhythm notifications by key (#182)
        out["ecg"] = ecg.merge(low.get("ecg"), high.get("ecg"))
    # ---- home readings and heights (#223): an export's and the app's laid together, a reading in both once
    for k, n in (("bp_readings", 3), ("temp_readings", 2)):
        if low.get(k) or high.get(k):
            out[k] = _readings(low.get(k), high.get(k), n)
    if low.get("heights") or high.get("heights"):   # [day, cm, source, unit]: one a day, high's
        by = {h[0]: h for src in (low, high) for h in src.get("heights") or [] if isinstance(h, list) and h}
        out["heights"] = [by[d] for d in sorted(by)]

    for lst, at in READINGS.items():   # readings one by one (#224): both kept, high's winning per minute, source and app
        if low.get(lst) and high.get(lst):
            ident = lambda r: (str(r[0])[:16], r[at - 1], r[at]) if isinstance(r, list) and len(r) > at else None
            mine = {ident(r) for r in high[lst]}
            out[lst] = sorted([r for r in low[lst] if ident(r) not in mine] + list(high[lst]),
                              key=lambda r: _by_start({"start": r[0] if isinstance(r, list) else ""}))
    return out


def _readings(low, high, n):
    """Two lists of home readings ([UTC time, value..., source, app]; n: how many leading fields say which reading it
    is) laid together, oldest first: a reading in both (the same minute and values, as the receiver counts them) is
    high's."""
    out, seen = [], set()
    for src in (high, low):
        for r in src or []:
            t = _instant(r[0]) if isinstance(r, list) and r else None
            if not isinstance(t, float):
                continue
            k = (int(t) // 60, *(round(v, 1) if isinstance(v, (int, float)) else None for v in r[1:n]))
            if k not in seen:
                seen.add(k)
                out.append((t, r))
    return [r for _, r in sorted(out, key=lambda x: x[0])]


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    crypto.write_file(path, json.dumps(obj, separators=(",", ":")).encode())


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _target(db, pid):
    """(where the merged imports go, the profile's watch_file now)."""
    r = db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()
    if r is None:
        raise ValueError(f"No profile {pid}.")
    current, own = r["watch_file"], own_path(pid)
    return (own if not current or _same(current, own) else side_path(pid)), current


def layers(db, pid):
    """The ids of the imports that have a summary of their own, oldest first (only those whose import still exists)."""
    d, ids = imports_dir(pid), []
    for n in os.listdir(d) if os.path.isdir(d) else ():
        if n.endswith(".json") and n[:-5].isdigit():
            ids.append(int(n[:-5]))
    if not ids:
        return []
    live = {r[0] for r in db.execute("SELECT id FROM imports WHERE profile_id=? AND id IN (SELECT value FROM json_each(?))",
                                     (pid, json.dumps(ids)))}
    return sorted(i for i in ids if i in live)


def rebuild(db, pid, prune=True):
    """Write the merged imports file again from the imports' own summaries; when nothing is left, remove it (and point
    watch_file back at none, if it was pointed at it). Returns the merged summary, or None. prune: also remove the ECG
    samples no import lists any more (#182; an undo does that only once it has committed)."""
    target, current = _target(db, pid)
    merged = _read(layer_path(pid))
    for iid in layers(db, pid):
        merged = merge(merged, _read(layer_path(pid, iid)))
    own = own_path(pid)
    if prune:
        _prune_waves(pid, merged)
    if merged is None:
        _remove(target)
        if target == own and current and _same(current, own):
            db.execute("UPDATE profiles SET watch_file=NULL WHERE id=?", (pid,))
        return None
    _write(target, merged)
    if target == own:
        db.execute("UPDATE profiles SET watch_file=? WHERE id=? AND watch_file IS NULL", (own, pid))
    return merged


# ---- ECG recordings' samples (#182): a file each, apart from the summary, read when a recording's drawer opens
def waves_dir(pid):
    return os.path.join(prof.profile_dir(pid), "watch", "ecg")


def _save_waves(pid, waves):
    for k, w in (waves or {}).items():
        name = ecg.file_name(k)
        if name and isinstance(w, dict):
            os.makedirs(waves_dir(pid), exist_ok=True)
            crypto.write_file(os.path.join(waves_dir(pid), name), ecg.pack(w))


def _prune_waves(pid, merged):
    """Remove the samples of recordings no import lists any more (an import undone)."""
    d = waves_dir(pid)
    if not os.path.isdir(d):
        return
    keep = {ecg.file_name(e.get("key")) for e in (merged or {}).get("ecg") or [] if isinstance(e, dict)}
    for n in os.listdir(d):
        if n.endswith(".json.gz") and n not in keep:
            _remove(os.path.join(d, n))


def wave_path(db, pid, name):
    """Where a recording's samples are, by its file name's 16 hex (ecg.NAME_RE), or None: beside the profile's watch
    file (a phone receiver's, watch/receiver.py) first, then the imports'."""
    if not ecg.NAME_RE.match(name or ""):
        return None
    r = db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()
    dirs = ([os.path.join(os.path.dirname(r["watch_file"]), "ecg")] if r and r["watch_file"] else []) + [waves_dir(pid)]
    for d in dirs:
        p = os.path.join(d, name + ".json.gz")
        if os.path.isfile(p):
            return p
    return None


def wave(db, pid, name):
    """A recording's samples (wave_path), or None."""
    p = wave_path(db, pid, name)
    return ecg.unpack(crypto.read_file(p)) if p else None


def save_import(db, pid, summary, import_id=None):
    """Store an imported summary for the profile, replacing an earlier import's overlapping days and workouts. With
    import_id (the records import it came with, jobs.py) it is kept as that import's own, so undoing the import takes
    it away again; without, it joins the legacy layer, which can't be undone. Returns {"path", "days", "workouts"}
    (days with at least one point, in this summary)."""
    target, _ = _target(db, pid)
    _migrate_profile(db, pid)
    summary = dict(summary)
    _save_waves(pid, summary.pop("ecg_waves", None))   # ECG samples (#182): files of their own, before the summary lists them
    from . import cycle_tracking   # an export's cycle records (#223): never in the summary, kept apart only while on
    cycle_tracking.save_imported(db, pid, import_id, summary.pop("cycle", None))
    if import_id is None:
        _write(layer_path(pid), merge(_read(layer_path(pid)), summary))
    else:
        _write(layer_path(pid, import_id), summary)
    rebuild(db, pid)
    # by date: a glucose meter's reading is keyed by its time (#223)
    days = {str(d)[:10] for m in summary.get("metrics") or [] for d, _ in m.get("pts") or []}
    readings = sum(len(summary.get(k) or []) for k in READINGS)   # #224
    return {"path": target, "days": len(days), "workouts": len(summary.get("exercise") or []), **ecg.counts(summary),
            **measure_counts(summary), **({"readings": readings} if readings else {})}   # (#223, #224)


def measure_counts(summary):
    """The home readings, glucose and heights in a summary, those it has (#223, for the import's counts): blood
    pressure and temperature readings, meter readings, days with a CGM's, and heights."""
    pts = {m.get("key"): m.get("pts") or [] for m in (summary or {}).get("metrics") or [] if isinstance(m, dict)}
    n = {"bp_readings": len((summary or {}).get("bp_readings") or []), "temp_readings": len((summary or {}).get("temp_readings") or []),
         "glucose_readings": len(pts.get("glucose_meter", [])), "cgm_days": len(pts.get("glucose_avg", [])),
         "heights": len((summary or {}).get("heights") or [])}
    return {k: v for k, v in n.items() if v}


def has_import(pid, import_id):
    """Whether that import brought watch data of its own."""
    return os.path.isfile(layer_path(pid, import_id))


def import_counts(pid, import_id):
    """{"watch_days", "workouts", "readings"} of what an import brought (its own summary), for the import history of
    an import with no job to say so (records typed in, #224); {} when it brought none."""
    s = _read(layer_path(pid, import_id)) or {}
    days = {d for m in s.get("metrics") or [] for d, _ in m.get("pts") or []}
    out = {"watch_days": len(days), "workouts": len(s.get("exercise") or []), "readings": sum(len(s.get(k) or []) for k in READINGS)}
    return {k: v for k, v in out.items() if v}


def forget_import(db, pid, import_id):
    """Take an import's watch data away, inside the undo's transaction and after its import row is deleted: its own
    summary is set aside and the merged file rebuilt without it. Returns None when it brought none, else a function
    to call once the transaction ends: finish(True) after the commit removes what was set aside; finish(False) after
    a rollback puts it back and rebuilds again."""
    path = layer_path(pid, import_id)
    if not os.path.isfile(path):
        return None
    aside = os.path.join(imports_dir(pid), f"{UNDOING}{int(import_id)}-{secrets.token_hex(4)}")
    os.replace(path, aside)
    from . import cycle_tracking   # its cycle records (#223) go with it
    cyc = cycle_tracking.imported_path(pid, import_id)
    cyc_aside = cyc + UNDOING + secrets.token_hex(4) if os.path.isfile(cyc) else None
    if cyc_aside:
        os.replace(cyc, cyc_aside)
    try:
        rebuild(db, pid, prune=False)
    except BaseException:
        os.replace(aside, path)
        if cyc_aside:
            os.replace(cyc_aside, cyc)
        raise

    def finish(committed):
        if committed:
            _remove(aside)
            if cyc_aside:
                _remove(cyc_aside)
            rebuild(db, pid)   # the ECG samples only the undone import listed go now (#182)
        else:
            os.replace(aside, path)
            if cyc_aside and os.path.isfile(cyc_aside):   # unless tracking was turned off meanwhile
                os.replace(cyc_aside, cyc)
            rebuild(db, pid)
    return finish


def _migrate_profile(db, pid):
    """Before imports were kept apart the merged file was their only copy: it becomes the legacy layer, once."""
    if os.path.isdir(imports_dir(pid)):
        return False
    target, _ = _target(db, pid)
    have = _read(target)
    if have is None:
        return False
    _write(layer_path(pid), have)
    return True


def migrate(db):
    """At start-up: move each profile's imported watch file from before #24 into its legacy layer, and drop the
    summaries of imports that no longer exist (one whose saving failed after its file was written, an undo stopped
    before it tidied up), rebuilding those profiles' merged files. Returns how many profiles were migrated."""
    n = 0
    for r in db.execute("SELECT id FROM profiles ORDER BY id").fetchall():
        pid = r["id"]
        n += _migrate_profile(db, pid)
        _tidy_cycle(db, pid)   # (#223)
        d = imports_dir(pid)
        if not os.path.isdir(d):
            continue
        live, stale = set(layers(db, pid)), False
        for name in os.listdir(d):
            if name.startswith(UNDOING) or (name.endswith(".json") and name[:-5].isdigit() and int(name[:-5]) not in live):
                _remove(os.path.join(d, name))
                stale = True
        if stale:
            rebuild(db, pid)
    return n


def _tidy_cycle(db, pid):
    """At start-up: drop the cycle records (#223) of imports that no longer exist, and of an undo stopped half-way."""
    from . import cycle_tracking
    d = cycle_tracking.imported_dir(pid)
    names = os.listdir(d) if os.path.isdir(d) else []
    ids = [int(x[:-5]) for x in names if x.endswith(".json") and x[:-5].isdigit()]
    live = {r[0] for r in db.execute("SELECT id FROM imports WHERE profile_id=? AND id IN (SELECT value FROM json_each(?))",
                                     (pid, json.dumps(ids)))} if ids else set()
    for x in names:
        if not (x.endswith(".json") and x[:-5].isdigit() and int(x[:-5]) in live):
            _remove(os.path.join(d, x))


def load(db, pid):
    """What the Health tab reads: the profile's watch file with any import kept beside it merged in (the watch
    file winning), or None when there is neither."""
    r = db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()
    if r is None:
        return None
    current, side = r["watch_file"], side_path(pid)
    base = _read(current)
    extra = None if current and _same(current, side) else _read(side)
    return merge(extra, base)
