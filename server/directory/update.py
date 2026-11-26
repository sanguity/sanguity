"""Adding and updating the provider directory's lists from the page (#992): Settings → Install → Provider directory.

Epic's and Oracle Health's published lists can't be shipped with Sanguity (build.py says why), so each server reads
its own copy. Until #992 an admin ran tools/build_directory.py on the host; now the server does it, in the
background, when an admin presses Add the lists (or Update now), and once a week if an admin turned that on
(the install setting directory.weekly, off by default). Nothing is contacted before either.

  One thread ("sanguity-directory", beside backup_jobs.py's) takes an admin's request, else the weekly update when
  it's due: WEEK after the last time a list was read (at once if none ever was), RETRY after a failed try.
  Each list is downloaded through net.py under outside.for_directory's permit (its host only: open.epic.com,
  raw.githubusercontent.com), so each contact is in outside_contacts and the install log, and saved in a folder of
  its own beside the copy; at most MAX_BYTES each. Then build.py, the code the command line runs, reads them in a
  process of its own (Epic's list is about 100 MB of JSON, and reading it takes several times that in memory, given
  back when that process ends) and writes the new copy, which replaces the old one in one step: the next search
  uses it, with no restart.
  A list that fails (no answer, cut short, an error, not the list) leaves what the earlier copy had of it in use, and
  the outcome says why; the other list is still read.

  status(db) -> what the page shows (GET /api/provider-directory)
  submit(who, ip)       an admin's Add the lists / Update now: queued for the thread (Busy while one runs)
  run(origin, who, ip)  one update, in the calling thread (the thread's, or a test's)

Progress is kept in memory (a run the app's stop cuts short is simply not finished: its folder goes at the next
run); the outcome of the last run, and when a list was last read, in the meta table. Every run is in the install's
activity log ("directory.update").
"""
import json, logging, os, shutil, subprocess, sys, tempfile, threading, time, urllib.parse

from .. import activity, db as dbm, net, outside, settings
from . import build, local_path

log = logging.getLogger("sanguity.directory")

TICK = 3600                      # seconds between looks at whether the weekly update is due
WEEK, RETRY = 7 * 86400, 86400   # due a week after a list was last read; a day after a failed try
MAX_BYTES = 512 * 1024 * 1024    # a list bigger than this is refused (Epic's was about 97 MB in November 2026)
CHUNK = 1 << 20
TIMEOUT = 60                     # seconds a host may go silent
READ_TIMEOUT = 900               # seconds build.py may take to read the lists
WEEKLY = activity.WEEKLY_DIRECTORY   # who the weekly update is, in the install log
STAGES = {"download": "Downloading", "read": "Reading the lists"}


class Busy(Exception):
    pass


_lock = threading.Lock()
_now = {}        # the run going on: {"origin", "stage", "source", "got", "size", "started"}
_queue = []      # an admin's request waiting for the thread: (who, ip)
_thread, _stop, _wake = None, threading.Event(), threading.Event()


def wake():
    _wake.set()


def start():
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="sanguity-directory", daemon=True)
    _thread.start()


def stop(timeout=5):
    global _thread
    _stop.set()
    _wake.set()
    if _thread is not None:
        _thread.join(timeout)
    _thread = None


def _loop():
    while not _stop.is_set():
        try:
            with _lock:
                asked = _queue.pop(0) if _queue else None
            if asked:
                run("manual", *asked)
            else:
                db = dbm.connect()
                try:
                    due = due_at(db)
                finally:
                    db.close()
                if due is not None and due <= time.time():
                    run("weekly")
        except Exception:
            log.exception("provider directory")
            with _lock:
                _now.clear()
        if not _queue:
            _wake.wait(TICK)
        _wake.clear()


# ---------------------------------------------------------------- the schedule

def _meta(db, k):
    try:
        return json.loads(dbm.meta_get(db, k) or "null")
    except ValueError:
        return None


def weekly(db=None):
    """Whether the weekly update is on: the value in force, as the rest of the server reads them (#1043), so off while
    saved settings are left out (SANGUITY_SAVED_SETTINGS=off) whatever is saved."""
    return bool(settings.install_value("directory.weekly"))


def due_at(db):
    """When the weekly update is next due (a Unix time), or None while it's off."""
    if not weekly(db):
        return None
    read, last = _meta(db, "directory.read_at"), _meta(db, "directory.last") or {}
    due = (read or 0) + WEEK
    if last.get("at") and last.get("failed"):   # a try that failed: again a day later, not every hour
        due = max(due, last["at"] + RETRY)
    return due


# ---------------------------------------------------------------- one update

def submit(who=None, ip=None):
    with _lock:
        if _now or _queue:
            raise Busy("The lists are being read already.")
        _queue.append((who, ip))
        _now.update(origin="manual", stage="queued", source=None, got=0, size=None, started=dbm.now())
    wake()


def _say(**kw):
    with _lock:
        _now.update(kw)


def _download(permit, key, url, dest):
    """url's list into dest, through the permit; raises with why in plain words (ValueError, or net's errors)."""
    u = urllib.parse.urlsplit(url)
    conn = net.connection(permit, url, timeout=TIMEOUT)
    try:
        conn.request("GET", (u.path or "/") + (f"?{u.query}" if u.query else ""),
                     headers={"User-Agent": build.UA, "Accept": "application/fhir+json, application/json"})
        r = conn.getresponse()
        if r.status != 200:
            raise ValueError(f"{net.host_of(url)} answered {r.status} {r.reason}".rstrip())
        size = r.getheader("Content-Length")
        size = int(size) if size and size.isdigit() else None
        if size and size > MAX_BYTES:
            raise ValueError(f"the list is bigger than {MAX_BYTES >> 20} MB")
        _say(size=size)
        got = 0
        with open(dest, "wb") as f:
            while True:
                part = r.read(CHUNK)
                if not part:
                    break
                got += len(part)
                if got > MAX_BYTES:
                    raise ValueError(f"the list is bigger than {MAX_BYTES >> 20} MB")
                f.write(part)
                _say(got=got)
        if size is not None and got != size:
            raise ValueError("the download was cut short")
    finally:
        conn.close()


def _why(e):
    if isinstance(e, (ValueError, net.Refused)):
        return str(e)
    if isinstance(e, net.Errors):
        if e.__class__.__name__ == "IncompleteRead":
            return "the download was cut short"
        return net.why(e)
    return "something went wrong; the app's log says what"


def run(origin="manual", who=None, ip=None):
    """Read both lists and write the new copy. -> the outcome, as status()'s "last"."""
    with _lock:
        _now.update(origin=origin, stage="download", source=None, got=0, size=None, started=_now.get("started") or dbm.now())
    db = dbm.connect()
    path = local_path()
    folder = os.path.dirname(path)
    read, failed = {}, {}
    try:
        os.makedirs(folder, exist_ok=True)
        for old in os.listdir(folder):   # what a run the app's stop cut short left
            if old.startswith(".update-"):
                shutil.rmtree(os.path.join(folder, old), ignore_errors=True)
        work = tempfile.mkdtemp(prefix=".update-", dir=folder)
        try:
            permit = outside.for_directory(db, who, ip, actor=None if who else WEEKLY)
            got = {}
            for key in build.FETCHED:
                dest = os.path.join(work, key + ".json")
                _say(stage="download", source=key, got=0, size=None)
                try:
                    _download(permit, key, build.SOURCES[key]["url"], dest)
                    got[key] = dest
                except Exception as e:
                    if not isinstance(e, (ValueError, net.Refused, *net.Errors)):
                        log.exception("provider directory: %s", key)
                    failed[key] = _why(e)
            if got:
                _say(stage="read", source=None, got=0, size=None)
                out = os.path.join(work, "providers.json.gz")
                args = [sys.executable, "-I", "-B", build.__file__, *[f"--from={k}={p}" for k, p in got.items()],
                        "--only-files", "--keep", path, "--report", "--out", out]
                try:
                    p = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=READ_TIMEOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    report = json.loads(p.stdout.decode().strip().splitlines()[-1])["sources"] if p.returncode == 0 else {}
                except (subprocess.TimeoutExpired, ValueError, IndexError, KeyError):
                    report = {}
                for k in got:
                    r = report.get(k) or {"error": "the list couldn't be read"}
                    if "entries" in r:
                        read[k] = r["entries"]
                    else:
                        failed[k] = r["error"]
                if read:
                    os.replace(out, path)   # one step: a search reads the old copy or the new, never half of one
        finally:
            shutil.rmtree(work, ignore_errors=True)
    except OSError as e:
        log.warning("provider directory: couldn't write to %s (%s)", folder, e.strerror or type(e).__name__)
        failed = {k: failed.get(k) or f"couldn't write to {folder} ({e.strerror or e})" for k in build.FETCHED if k not in read}
    finally:
        with _lock:
            _now.clear()
    now = dbm.now()
    last = {"at": now, "origin": origin, "read": read, "failed": failed}
    try:
        with dbm.transaction(db):
            dbm.meta_set(db, "directory.last", json.dumps(last))
            if read:
                dbm.meta_set(db, "directory.read_at", json.dumps(now))
            activity.log(db, "directory.update", who, None, {"origin": origin, "read": read, "failed": failed}, ip,
                         actor=None if who else WEEKLY)
    finally:
        db.close()
    return last


# ---------------------------------------------------------------- what the page shows

def status(db):
    from . import index
    ix = index()
    have = {s.get("id"): s for s in ix.sources if isinstance(s, dict)}
    lists = []
    for key, s in build.SOURCES.items():
        h = have.get(key) or {}
        lists.append({"id": key, "label": s["label"], "name": s.get("name"), "terms": s["terms"], "fetched": bool(s["url"]),
                      "host": net.host_of(s["url"]) if s["url"] else None, "size": s.get("size"), "adds": s.get("adds"),
                      "included": bool(h.get("included")), "entries": h.get("entries"), "retrieved": h.get("retrieved")})
    with _lock:
        now = dict(_now)
    if now:
        now["stage_label"] = STAGES.get(now.get("stage"), "Starting")
        now["name"] = build.SOURCES.get(now.get("source") or "", {}).get("name")
    return {"total": len(ix.entries), "lists": lists, "hosts": sorted(outside.directory_hosts()), "running": now or None,
            "last": _meta(db, "directory.last"), "read_at": _meta(db, "directory.read_at"),
            "weekly": weekly(db), "next": due_at(db)}
