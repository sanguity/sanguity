"""Backups from the Admin page (#200): Back up now, a schedule the app keeps, and Check a backup, each run in the
background so no request waits for them.

One thread ("sanguity-backups", beside jobs.py's and sync.py's) takes the queued rows of backup_runs (schema version
28) in turn, then makes the schedule's backup when one is due. It wakes every TICK seconds, or at once when an admin
asks for something. One backup or check runs at a time; the page polls GET /api/backups for how it goes.

  Back up now, and the schedule's   backup.create() into BACKUP_DIR, safe while the app runs. While backups are
                                    scheduled, the oldest of Sanguity's own backup files there then go, keeping the
                                    newest "keep" (backup.prune); nothing else in the folder is touched
  Check a backup                    backup.check(): restored into a temporary folder in BACKUP_DIR, compared with its
                                    manifest, then deleted. It never writes to DATA_DIR

The schedule (off, daily or weekly) and how many backups to keep are in the meta table. A backup is due when the
newest in the folder is INTERVAL old, whoever made it (Back up now, the schedule, or server.manage backup from cron);
after a failure the next try is RETRY later. Late: the newest backup is older than LATE for the schedule (with the
schedule off too: backups may come from cron), or the last one the app tried failed. The Admin page and its menu
item say so (status, late).

At start-up (recover): rows left queued or running never finished. A backup whose file was completed is marked done
(a stop just after writing it, or a database restored from that very backup, which holds its own row as running);
the others failed. What they left in the backups folder goes (backup.clean_leftovers).

Every backup, check, failure and change of schedule is in the install's activity log (activity.py: backup.*).
"""
import json, logging, os, shutil, threading, time

from . import activity, backup, config, db as dbm

log = logging.getLogger("sanguity.backups")

TICK = 60                                         # seconds between looks at the schedule
SCHEDULES = ("off", "daily", "weekly")
INTERVAL = {"daily": 86400, "weekly": 7 * 86400}  # a backup is due when the newest is this old
LATE = {"daily": 30 * 3600, "weekly": 8 * 86400, "off": 8 * 86400}   # ...and late when it is this old
RETRY = 3600                                      # after a failed scheduled backup, the next try
KEEP_DEFAULT, KEEP_MAX = 14, 365
RUNS_KEPT = 100                                   # rows of backup_runs kept; older ones go
INTERRUPTED = "Interrupted: the app stopped while this ran. Nothing was changed; start it again."


class Busy(Exception):
    pass


_thread, _stop, _wake, _ips = None, threading.Event(), threading.Event(), {}   # _ips: run id -> who asked's address


def wake():
    _wake.set()


def start():
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="sanguity-backups", daemon=True)
    _thread.start()


def stop(timeout=5):
    """Ask the thread to stop after what it's doing. A backup still running when the process ends is recovered."""
    global _thread
    _stop.set()
    _wake.set()
    if _thread is not None:
        _thread.join(timeout)
    _thread = None


def _loop():
    while not _stop.is_set():
        try:
            run_queued()
            run_due()
        except Exception:
            log.exception("backups")
        _wake.wait(TICK)
        _wake.clear()


# ---------------------------------------------------------------- the schedule, and what's in the folder

def schedule(db):
    """(off | daily | weekly, how many backups to keep)."""
    s, k = dbm.meta_get(db, "backup.schedule"), dbm.meta_get(db, "backup.keep")
    return (s if s in SCHEDULES else "off"), (int(k) if k and k.isdigit() and 1 <= int(k) <= KEEP_MAX else KEEP_DEFAULT)


def set_schedule(db, sched, keep):
    if sched not in SCHEDULES:
        raise ValueError("The schedule is off, daily or weekly.")
    if not isinstance(keep, int) or not 1 <= keep <= KEEP_MAX:
        raise ValueError(f"Keep between 1 and {KEEP_MAX} backups.")
    dbm.meta_set(db, "backup.schedule", None if sched == "off" else sched)
    dbm.meta_set(db, "backup.keep", None if keep == KEEP_DEFAULT else str(keep))


def files(dest=None):
    """Sanguity's backups in the folder, newest first: [{"name", "size", "at"}]."""
    out = []
    for p in reversed(backup.list_backups(dest)):
        try:
            out.append({"name": os.path.basename(p), "size": os.path.getsize(p), "at": backup.made_at(p)})
        except (OSError, ValueError):
            continue
    return out


def removes(n_files, sched, keep):
    """How many of the backups there are now the next backup would remove, with this schedule."""
    return max(0, n_files + 1 - keep) if sched != "off" else 0


def _row(r):
    d = dict(r)
    d["result"] = json.loads(d["result"]) if d["result"] else None
    d["ok"] = None if d["ok"] is None else bool(d["ok"])
    return d


def _busy(db):
    r = db.execute("SELECT * FROM backup_runs WHERE state IN ('queued', 'running') ORDER BY id LIMIT 1").fetchone()
    return _row(r) if r else None


def _failing(db, newest):
    """The app's last backup, if it failed and nothing has made one since."""
    r = db.execute("SELECT * FROM backup_runs WHERE kind='backup' AND state IN ('done', 'failed') ORDER BY id DESC LIMIT 1").fetchone()
    if r and r["state"] == "failed" and (not newest or r["created"] >= newest["at"]):
        return r
    return None


def due_at(db, newest=None, now=None):
    """When the schedule's next backup is due (epoch seconds), or None while it's off."""
    sched, _ = schedule(db)
    if sched == "off":
        return None
    if newest is None:
        fs = files()
        newest = fs[0] if fs else None
    at = newest["at"] + INTERVAL[sched] if newest else 0
    fail = _failing(db, newest)
    if fail:
        at = max(at, (fail["finished"] or fail["created"]) + RETRY)
    return at


def _age(seconds):
    days, hours = seconds // 86400, seconds // 3600
    return f"{days} day{'s' if days != 1 else ''}" if days >= 2 else f"{hours} hour{'s' if hours != 1 else ''}"


def _state(db, fs, now):
    """(state, warning): "ok", "none" (no backup yet), "late" or "failed", and what to say when it's late or failed."""
    sched, _ = schedule(db)
    newest = fs[0] if fs else None
    fail = _failing(db, newest)
    if fail:
        return "failed", f"The last backup failed: {fail['message'] or 'no reason given.'}"
    if not newest:
        return "none", None
    age = now - newest["at"]
    if age <= LATE[sched]:
        return "ok", None
    if sched == "off":
        return "late", (f"The newest backup is {_age(age)} old, and the app isn't scheduled to make them. If a job on the "
                        "server makes them (server.manage backup), check it's still running, or turn on a schedule here.")
    return "late", (f"The newest backup is {_age(age)} old, though backups are scheduled {sched}. Check the app has been "
                    "running and the backups folder is there and has space.")


def late(db):
    """Whether the Admin page warns about backups (for the Admin menu item, main.me)."""
    return _state(db, files(), dbm.now())[0] in ("late", "failed")


def status(db, now=None):
    """What the Admin page's Backups shows: the schedule, the newest backup (when, how big, where), whether it's
    late, the backups in the folder with their last check, what's running, and the latest runs."""
    now = now or dbm.now()
    sched, keep = schedule(db)
    fs = files()
    state, warning = _state(db, fs, now)
    checks = {}
    for r in db.execute("SELECT file, ok, finished FROM backup_runs WHERE kind='check' AND state='done' ORDER BY id"):
        checks[r["file"]] = {"ok": bool(r["ok"]), "at": r["finished"]}
    for f in fs:
        f["checked"] = checks.get(f["name"])
    try:
        free = shutil.disk_usage(config.BACKUP_DIR).free
    except OSError:
        free = None
    data = os.path.realpath(config.DATA_DIR)
    return {"schedule": sched, "keep": keep, "keep_max": KEEP_MAX, "state": state, "warning": warning,
            "late": state in ("late", "failed"), "newest": fs[0] if fs else None, "backups": fs,
            "total_size": sum(f["size"] for f in fs), "folder": config.BACKUP_DIR, "free": free,
            "inside_data": os.path.realpath(config.BACKUP_DIR).startswith(data + os.sep),
            "next": due_at(db, fs[0] if fs else None, now), "running": _busy(db),
            "runs": [_row(r) for r in db.execute("SELECT * FROM backup_runs ORDER BY id DESC LIMIT 10")],
            "key_file": config.KEY_FILE}


# ---------------------------------------------------------------- asking for a backup or a check

def submit(db, kind, who=None, ip=None, name=None, origin="manual"):
    """Queue a backup (kind "backup") or a check of the backup called name; returns the run's id. Raises Busy while
    another is queued or running. who: the admin who asked (a session row), or None for the schedule."""
    with dbm.transaction(db):
        if _busy(db):
            raise Busy("A backup or a check is running already. Wait for it to finish, then try again.")
        rid = db.execute("""INSERT INTO backup_runs(kind, origin, state, file, created_by, created_name, created)
                            VALUES(?, ?, 'queued', ?, ?, ?, ?)""",
                         (kind, origin, name, who["id"] if who else None,
                          who["username"] if who else activity.SCHEDULED_BACKUP, dbm.now())).lastrowid
        db.execute("DELETE FROM backup_runs WHERE id <= ?", (rid - RUNS_KEPT,))
    _ips[rid] = ip
    wake()
    return rid


def run_queued():
    """Run what admins asked for, oldest first. Returns how many ran."""
    db = dbm.connect()
    try:
        ids = [r[0] for r in db.execute("SELECT id FROM backup_runs WHERE state='queued' ORDER BY id")]
    finally:
        db.close()
    for rid in ids:
        if _stop.is_set():
            break
        run(rid)
    return len(ids)


def run_due(now=None):
    """Make the schedule's backup if one is due. Returns whether it did."""
    db = dbm.connect()
    try:
        at = due_at(db, now=now)
        if at is None or at > (now or dbm.now()) or _busy(db):
            return False
        rid = submit(db, "backup", origin="schedule")
    except Busy:
        return False
    finally:
        db.close()
    run(rid)
    return True


def _progress(db, rid):
    last = [0.0]

    def say(stage, done, total):
        t = time.monotonic()
        if t - last[0] < 0.5 and done < total:
            return
        last[0] = t
        db.execute("UPDATE backup_runs SET stage=?, done=?, total=? WHERE id=?", (stage, done, total, rid))
    return say


def run(rid):
    """Run one queued backup or check (in the thread, or a test)."""
    db = dbm.connect()
    try:
        if db.execute("UPDATE backup_runs SET state='running', started=? WHERE id=? AND state='queued'",
                      (dbm.now(), rid)).rowcount != 1:
            return   # someone else took it
        r = db.execute("SELECT * FROM backup_runs WHERE id=?", (rid,)).fetchone()
        who = {"id": r["created_by"], "username": r["created_name"]} if r["created_by"] else None
        note = lambda action, detail: activity.log(db, action, who, detail=detail, ip=_ips.get(rid),
                                                   actor=None if who else r["created_name"])
        (_backup if r["kind"] == "backup" else _check)(db, r, note)
    except Exception:
        log.exception("backup run %s", rid)
        db.execute("UPDATE backup_runs SET state='failed', message=?, finished=? WHERE id=? AND state='running'",
                   ("Something went wrong; the app's log says what.", dbm.now(), rid))
    finally:
        _ips.pop(rid, None)
        db.close()


def _plain(e):
    if isinstance(e, backup.BackupError):
        return str(e)
    if isinstance(e, OSError):
        return f"Couldn't write to the backups folder {config.BACKUP_DIR} ({e.strerror or e})."
    return f"{e.__class__.__name__}: {e}"


def _backup(db, r, note):
    rid = r["id"]
    fs = files()
    failing_before = _failing(db, fs[0] if fs else None) is not None
    try:
        name = backup.new_name()
        # named before the database is copied, so a database restored from this backup knows its row's file (recover)
        db.execute("UPDATE backup_runs SET file=?, stage='archiving' WHERE id=?", (name, rid))
        say = _progress(db, rid)
        path = backup.create(name=name, progress=lambda d, t: say("archiving", d, t))
        size = os.path.getsize(path)
        sched, keep = schedule(db)
        removed = backup.prune(keep) if sched != "off" else []
    except Exception as e:
        msg = _plain(e)
        if not isinstance(e, (backup.BackupError, OSError)):
            log.exception("backup %s", rid)
        db.execute("UPDATE backup_runs SET state='failed', message=?, finished=? WHERE id=?", (msg, dbm.now(), rid))
        if not failing_before:   # once per spell of failures, not at every retry
            note("backup.fail", {"error": msg[:300]})
        return
    db.execute("UPDATE backup_runs SET state='done', size=?, result=?, stage=NULL, finished=? WHERE id=?",
               (size, json.dumps({"removed": removed}), dbm.now(), rid))
    note("backup.create", {"name": name, "size": size, **({"removed": len(removed)} if removed else {})})


def _check(db, r, note):
    rid, path = r["id"], os.path.join(config.BACKUP_DIR, r["file"] or "")
    if not (r["file"] and backup.NAME.match(r["file"]) and os.path.isfile(path)):
        db.execute("UPDATE backup_runs SET state='failed', message=?, finished=? WHERE id=?",
                   ("That backup isn't in the backups folder any more.", dbm.now(), rid))
        return
    say = _progress(db, rid)
    got = backup.check(path, say)
    db.execute("UPDATE backup_runs SET state='done', ok=?, message=?, result=?, stage=NULL, finished=? WHERE id=?",
               (int(got["ok"]), " ".join(got["problems"]) or None, json.dumps(got), dbm.now(), rid))
    note("backup.check", {"name": r["file"], "ok": got["ok"]})


# ---------------------------------------------------------------- start-up

def recover(db):
    """Rows a stop left queued or running, and what they left in the backups folder. Returns how many failed."""
    failed = 0
    for r in db.execute("SELECT * FROM backup_runs WHERE state IN ('queued', 'running')").fetchall():
        path = os.path.join(config.BACKUP_DIR, r["file"] or "")
        if r["kind"] == "backup" and r["file"] and backup.NAME.match(r["file"]) and os.path.isfile(path):
            db.execute("UPDATE backup_runs SET state='done', size=?, stage=NULL, finished=IFNULL(finished, ?) WHERE id=?",
                       (os.path.getsize(path), dbm.now(), r["id"]))
        else:
            db.execute("UPDATE backup_runs SET state='failed', stage=NULL, message=?, finished=? WHERE id=?",
                       (INTERRUPTED, dbm.now(), r["id"]))
            failed += 1
    try:
        backup.clean_leftovers()
    except OSError:
        log.exception("cleaning up the backups folder")
    return failed
