"""Check for new versions (#1159): off by default. Once an admin turns it on (the install setting updates.check, Install →
General → This server), the server asks GitHub's releases API for Sanguity's latest release, at most once a day, as an
admin's page loads (/api/me starts it in the background; nothing waits for it), and tells admins when it's newer than
the version this server runs. The only request: GET of RELEASES at its host alone (HOST), through net.py's gateway
under a permit that names no host while it's off, listed under Outside connections (PURPOSE). Nothing about this
server or anyone is sent: no version, no identifier, no cookie; GitHub sees the request's address, as for any visit.

  check(db)        ask now (the background thread's): {"latest", "url"} kept in meta, or why it failed
  state(db)        {"on", "current", "latest", "url", "newer", "checked", "problem"}: what the page shows
  maybe_check(db)  from /api/me, for an admin: a check in a thread of its own when it's on and the last is a day old
"""
import json, re, threading, time

from . import config, db as dbm, general, net, outside, settings

PURPOSE = "Update check"
HOST = "api.github.com"
RELEASES = "https://api.github.com/repos/sanguity/sanguity/releases/latest"
RELEASES_PAGE = "https://github.com/sanguity/sanguity/releases/"
META = "updates.latest"
EVERY = 86400
MOST = 256 * 1024   # bytes of the answer read
_lock = threading.Lock()
_running = []


def on():
    return bool(settings.install_value("updates.check"))


def hosts():
    return {HOST} if on() else set()


def permit(db, who=None, ip=None):
    return net.Permit(PURPOSE, hosts, lambda host, purpose: outside.record(db, None, host, purpose, who, ip))


def _parts(v):
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", (v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else None


def check(db, who=None, ip=None):
    """Ask GitHub for the latest release; kept in meta with when, or why it failed."""
    kept = {"checked": dbm.now()}
    try:
        conn = net.connection(permit(db, who, ip), RELEASES, timeout=config.setting("NOTIFY_TIMEOUT"))
        try:
            conn.request("GET", "/repos/sanguity/sanguity/releases/latest",
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "Sanguity"})
            r = conn.getresponse()
            body = r.read(MOST + 1)
        finally:
            conn.close()
        if r.status != 200 or len(body) > MOST:
            raise ValueError(f"GitHub answered {r.status}")
        doc = json.loads(body)
        tag = str(doc.get("tag_name") or "")[:40]
        if not _parts(tag):
            raise ValueError("GitHub's latest release has no version number Sanguity reads")
        url = str(doc.get("html_url") or "")
        kept.update(latest=tag.lstrip("v"), url=url if url.startswith(RELEASES_PAGE) and len(url) < 300 else RELEASES_PAGE)
    except net.Refused as e:
        kept["problem"] = str(e)
    except net.Errors as e:
        kept["problem"] = f"GitHub couldn't be reached: {net.why(e)}."
    except ValueError as e:
        kept["problem"] = f"The answer couldn't be read: {e}."
    old = _kept(db)
    if "problem" in kept and old.get("latest"):   # a failed check keeps what the last one found
        kept.update(latest=old["latest"], url=old.get("url"))
    dbm.meta_set(db, META, json.dumps(kept))
    return kept


def _kept(db):
    try:
        return json.loads(dbm.meta_get(db, META) or "{}")
    except ValueError:
        return {}


def state(db):
    k = _kept(db)
    current = general.version()["version"]
    newer = bool(on() and k.get("latest") and _parts(current) and _parts(k["latest"]) > _parts(current))
    return {"on": on(), "current": current, "latest": k.get("latest"), "url": k.get("url"), "newer": newer,
            "checked": k.get("checked"), "problem": k.get("problem"), "host": HOST}


def maybe_check(db):
    """For an admin's /api/me: a check in the background when it's on and the last was EVERY ago or more."""
    if not on() or dbm.now() - (_kept(db).get("checked") or 0) < EVERY:
        return
    with _lock:
        if _running:
            return
        _running.append(time.monotonic())

    def run():
        d = dbm.connect()
        try:
            check(d)
        except Exception:
            pass
        finally:
            d.close()
            _running.clear()
    threading.Thread(target=run, name="sanguity-update-check", daemon=True).start()
