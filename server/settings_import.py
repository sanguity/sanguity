"""(#1158) The settings file is the one source of truth, as Sonarr's and Radarr's: the environment sets none of the
server's settings any more, only the container's own (config.CONTAINER). On the first start of this version, each
setting the environment set (the install settings a variable stands for, and the write-only secrets of
config.SECRETS) is copied into the database once, as the app starts (main.py's lifespan): the value it set was the one
in force until now. The restart settings are copied into data/server-settings.json the same way, earlier, as the
server starts (server_settings.take_environment). The copy is logged in Install activity, and General says once which
were taken (notice()). From then on one the environment sets is ignored; one changed since it was taken in is said in
the log at each start, naming it. What's kept of each one's text is a fingerprint (config.fingerprint), never the text.

The way back stays python -m server.manage config reset NAME|--all, and config set NAME VALUE (manage.py)."""
import json, os, time

from . import activity, config, crypto, db as dbm, server_settings, settings

MARK = "settings.taken_from_environment"   # meta: {"at", "seen": {NAME: fingerprint}, "names": [...], "shown": bool}
WHERE = "Settings → Install"


def _set():
    """{NAME: text} of the variables the environment sets that a page setting or a secret stands for."""
    names = [s.env for s in settings.SETTINGS.values() if s.env] + list(config.SECRETS)
    return {n: t for n in names if (t := os.environ.get(n, "").strip())}


def _secret(db, name, text):
    """A secret into its own write-only store, as the page saves it; False where it can't be kept."""
    if name == "OIDC_CLIENT_SECRET":
        from . import oidc
        dbm.meta_set(db, oidc.SECRET_META, crypto.enc(text))
        oidc._cache.clear()
        return True
    from . import notify
    t = notify.clean_token(text)
    if not t or not notify.configured():
        return False
    # (kept for the server it was first sent to, where the environment's token had gone already)
    kept = notify._meta(db, notify.ENV_META) or {}
    import hashlib
    there = kept.get("origin") if kept.get("hash") == hashlib.sha256(t.encode()).hexdigest() else None
    dbm.meta_set(db, notify.TOKEN_META, crypto.enc(json.dumps({"origin": there or notify.origin(), "token": t})))
    dbm.meta_set(db, notify.ENV_META, None)
    return True


def take(db, say=print, now=None):
    """As the app starts: the first time, copy what the environment sets into the database, log it, and record it;
    every time after, warn about each one changed since. Returns the names taken in (this time)."""
    seen = _set()
    have = dbm.meta_get(db, MARK)
    if have:
        try:
            before = json.loads(have).get("seen") or {}
        except ValueError:
            before = {}
        for n, t in seen.items():
            if before.get(n) != config.fingerprint(t):
                say("Warning: " + config.environment_changed(n, WHERE))
        return []
    by_env = {s.env: s for s in settings.SETTINGS.values() if s.env}
    names, changes = [], []
    saved = settings.stored(db, "install")
    with dbm.transaction(db):
        for n, t in seen.items():
            if n in config.SECRETS:
                if _secret(db, n, t):
                    names.append(n)
                    changes.append({"setting": n, "label": config.SECRETS[n]})   # (never its value)
                else:
                    say(f"{n} in the environment isn't taken in: there's no notification server to keep it for, or it "
                        "isn't a token Sanguity can send. Give it on Settings → Install → General → Notifications.")
                continue
            s = by_env[n]
            try:
                v = config.VARS[n].read(t)
                v = s.clean(list(v) if isinstance(v, tuple) else v)
            except (ValueError, TypeError):
                say(f"{n} in the environment ({t[:40]!r}) isn't a value {s.label!r} takes, so it isn't taken in: it's "
                    f"the value saved in the app, or the default; change it on {WHERE}.")
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
        dbm.meta_set(db, MARK, json.dumps({"at": int(now or time.time()), "names": names, "shown": not names,
                                           "seen": {n: config.fingerprint(t) for n, t in seen.items()}}))
        if names:
            activity.log(db, "settings.install", detail={"taken": names, "changes": changes}, actor=activity.START_UP)
    settings.forget_saved()
    if names:
        say(f"Took these settings from the environment into the settings, once: {', '.join(names)}. From now on they're "
            f"changed on {WHERE} (or with python -m server.manage config set NAME VALUE); a change to them in the "
            "environment is ignored.")
    return names


def notice(db):
    """General's word, once: {"at", "names": [{"name", "label"}]} of what was taken from the environment, until an
    admin has seen it (seen()); else None."""
    try:
        m = json.loads(dbm.meta_get(db, MARK) or "null")
    except ValueError:
        return None
    if not m or m.get("shown") or not m.get("names"):
        return None
    labels = {s.env: s.label for s in settings.SETTINGS.values() if s.env}
    labels.update({n: f"{d.label} ({d.group})" for n, d in server_settings.DEFS.items()}, **config.SECRETS)
    return {"at": m.get("at"), "names": [{"name": n, "label": labels.get(n, n)} for n in m["names"]]}


def seen(db):
    """General's word has been read: not shown again."""
    m = json.loads(dbm.meta_get(db, MARK) or "{}")
    if m and not m.get("shown"):
        dbm.meta_set(db, MARK, json.dumps({**m, "shown": True}))
