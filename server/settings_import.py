"""(#1158) The settings file is the one source of truth, as Sonarr's and Radarr's: the environment sets none of the
server's settings any more, only the container's own (config.CONTAINER) and, where an install opts in, the few it may
manage there (config.MANAGED: the sign-in provider, the notification token), which keep winning and are never taken in.
On the first start of this version, each setting the environment set (the install settings a variable stands for) is
copied into the database once, as the app starts (main.py's lifespan): the value it set was the one
in force until now. The restart settings are copied into data/server-settings.json the same way, earlier, as the
server starts (server_settings.take_environment). The copy is logged in Install activity, and General says once which
were taken (notice()). From then on one the environment sets is ignored; one changed since it was taken in is said in
the log at each start, naming it. What's kept of each one's text is a fingerprint (config.fingerprint), never the text.

The way back stays python -m server.manage config reset NAME|--all, and config set NAME VALUE (manage.py)."""
import json, os, time, urllib.parse

from . import activity, config, db as dbm, server_settings, settings

MARK = "settings.taken_from_environment"   # meta: {"at", "seen": {NAME: fingerprint}, "names": [...], "shown": bool}
WHERE = "Settings → Install"


def _set():
    """{NAME: text} of the variables the environment sets that a page setting stands for."""
    names = [s.env for s in settings.SETTINGS.values() if s.env]
    return {n: t for n in names if (t := os.environ.get(n, "").strip())}


def _shown(v):
    return ", ".join(map(str, v)) or "none" if isinstance(v, (list, tuple)) else str(v)


def salvage(s, text):
    """(#1158's review) What an install setting takes of the environment's text, never weaker than the text: (value,
    None) as given; (value, note) where it had to be adjusted: a number past its bounds is the nearest bound (searched
    upward where a gap lies between, as idle sign-out's 1 to 4 minutes: off is the weakest), an address list keeps each
    address's scheme and host[:port] (a path dropped) and the ones that pass; (None, note) where nothing of it can be
    taken (then what was in force before stays: the environment's unreadable text never was)."""
    var = config.VARS[s.env]
    try:
        v = var.read(text)
        return s.clean(list(v) if isinstance(v, tuple) else v), None
    except (ValueError, TypeError):
        pass
    lo, hi = getattr(s.check, "lo", None), getattr(s.check, "hi", None)
    hi = hi() if callable(hi) else hi
    if lo is not None and s.kind == "number":
        try:
            n = float(text.strip())
        except ValueError:
            n = None
        if n is not None and n == n:   # (not NaN)
            want = int(min(max(round(n), lo), hi))
            for v in [want] + list(range(want + 1, hi + 1)):
                try:
                    v = s.clean(v)
                except (ValueError, TypeError):
                    continue
                return v, f"{v} is used: {text[:40]} is past what it takes ({lo} to {hi}), and {v} is the nearest it does"
    if s.kind == "origins":
        kept = []
        for part in (p for p in text.replace(",", " ").split() if p):
            u = urllib.parse.urlsplit(part if "://" in part else "https://" + part)
            for cand in (part, f"{u.scheme}://{u.netloc}" if u.netloc else None):
                try:
                    for o in s.clean([cand]) if cand else ():
                        if o not in kept:
                            kept.append(o)
                    break
                except (ValueError, TypeError):
                    continue
        if kept:
            kept = s.clean(kept[:20])
            return kept, f"{_shown(kept)} is used: each address's scheme and name (a path, or what it didn't take, left out)"
    return None, f"not taken: {text[:40]!r} isn't a value it takes, so what it had stays"


def take(db, say=print, now=None):
    """As the app starts: the first time, copy what the environment sets into the database, log it, and record it;
    every time after, warn about each one changed since, and each that couldn't be taken as it's set ("adjusted",
    left out of "seen": warned about at every start until it's put right). Returns the names taken in (this time)."""
    seen = _set()
    have = dbm.meta_get(db, MARK)
    if have:
        try:
            m = json.loads(have)
        except ValueError:
            m = {}
        before, adjusted = m.get("seen") or {}, m.get("adjusted") or {}
        for n, t in seen.items():
            if n in adjusted:
                say(f"Warning: {n} in the environment couldn't be taken in as it's set: {adjusted[n]}. Put it right on "
                    f"{WHERE} and take {n} out of the environment.")
            elif before.get(n) != config.fingerprint(t):
                say("Warning: " + config.environment_changed(n, WHERE))
        return []
    by_env = {s.env: s for s in settings.SETTINGS.values() if s.env}
    names, changes, adjusted = [], [], {}
    saved = settings.stored(db, "install")
    with dbm.transaction(db):
        for n, t in seen.items():
            s = by_env[n]
            v, note = salvage(s, t)
            if note:
                adjusted[n] = note
                say(f"{n} in the environment couldn't be taken in as it's set: {note}. Put it right on {WHERE}.")
            if v is None:
                continue
            before = saved.get(s.key, s.default)
            if v == s.default:
                settings.clear(db, [s.key], "install")
            else:
                settings.put(db, s.key, v)
            if v != s.default:
                names.append(n)
            if v != before:
                changes.append({"setting": s.key, "label": s.label, "from": before, "to": v})
        restart = server_settings.taken() or {}
        for n in restart.get("names") or []:
            d = server_settings.DEFS[n]
            names.append(n)
            changes.append({"setting": n, "label": f"{d.label} ({d.group})", "to": server_settings.lookup(n)[1]})
        adjusted.update({n: note for n, note in (restart.get("adjusted") or {}).items() if n not in adjusted})
        dbm.meta_set(db, MARK, json.dumps({"at": int(now or time.time()), "names": names, "shown": not (names or adjusted),
                                           "adjusted": adjusted,
                                           "seen": {n: config.fingerprint(t) for n, t in seen.items() if n not in adjusted}}))
        if names or adjusted:
            activity.log(db, "settings.install", detail={"taken": names, "changes": changes,
                                                         **({"adjusted": sorted(adjusted)} if adjusted else {})}, actor=activity.START_UP)
    settings.forget_saved()
    if names:
        say(f"Took these settings from the environment into the settings, once: {', '.join(names)}. From now on they're "
            f"changed on {WHERE} (or with python -m server.manage config set NAME VALUE); a change to them in the "
            "environment is ignored.")
    return names


def notice(db):
    """General's word, once: {"at", "names": [{"name", "label", "note"?}]} of what was taken from the environment, and
    of what couldn't be taken as it was set (its note: what was used instead), until an admin has seen it (seen());
    else None."""
    try:
        m = json.loads(dbm.meta_get(db, MARK) or "null")
    except ValueError:
        return None
    adjusted = (m or {}).get("adjusted") or {}
    if not m or m.get("shown") or not (m.get("names") or adjusted):
        return None
    labels = {s.env: s.label for s in settings.SETTINGS.values() if s.env}
    labels.update({n: f"{d.label} ({d.group})" for n, d in server_settings.DEFS.items()})
    names = list(m.get("names") or []) + [n for n in adjusted if n not in (m.get("names") or [])]
    return {"at": m.get("at"), "names": [{"name": n, "label": labels.get(n, n), **({"note": adjusted[n]} if n in adjusted else {})}
                                         for n in names]}


def seen(db):
    """General's word has been read: not shown again."""
    m = json.loads(dbm.meta_get(db, MARK) or "{}")
    if m and not m.get("shown"):
        dbm.meta_set(db, MARK, json.dumps({**m, "shown": True}))
