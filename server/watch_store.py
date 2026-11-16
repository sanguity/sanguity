"""A profile's watch and scale summary: what the Health tab reads, from a phone receiver and from imports.

The summary is the shape watch/receiver.py writes as daily.json: {"metrics": [{key, name, unit, group,
pts: [[day, value]]}], "exercise": [{start, end, name, kind, minutes, key?}], ...}. A daily point is identified by
(metric key, day) and a workout by its `key` (when it has one) or else its start time, so a later import that
overlaps an earlier one replaces those days and workouts instead of adding to them.

Where it is kept, in the profile's own folder (so encryption at rest, deletion and backups cover it):
  <profile_dir>/watch/daily.json         an import into a profile with no watch file of its own; profiles.watch_file
                                         is pointed at it
  <profile_dir>/watch/apple-health.json  an import into a profile that already reads another file (a receiver's),
                                         kept beside it and merged in when read, the receiver winning
Both are sealed with the app's key (crypto.write_file).
"""
import json, os
from datetime import datetime, timezone

from . import crypto, profiles as prof

SIDE = "apple-health.json"


def own_path(pid):
    return os.path.join(prof.profile_dir(pid), "watch", "daily.json")


def side_path(pid):
    return os.path.join(prof.profile_dir(pid), "watch", SIDE)


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
    Either may be None. Top-level fields (updated, tz, last_payload...) come from `high` where it has them."""
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
    return out


def save_import(db, pid, summary):
    """Store an imported summary for the profile, replacing an earlier import's overlapping days and workouts.
    Returns {"path", "days", "workouts"} (days with at least one point, in this summary)."""
    r = db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()
    if r is None:
        raise ValueError(f"No profile {pid}.")
    current, own = r["watch_file"], own_path(pid)
    target = own if not current or _same(current, own) else side_path(pid)
    merged = merge(_read(target), summary)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    crypto.write_file(target, json.dumps(merged, separators=(",", ":")).encode())
    if target == own:
        db.execute("UPDATE profiles SET watch_file=? WHERE id=? AND watch_file IS NULL", (own, pid))
    days = {d for m in summary.get("metrics") or [] for d, _ in m.get("pts") or []}
    return {"path": target, "days": len(days), "workouts": len(summary.get("exercise") or [])}


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
