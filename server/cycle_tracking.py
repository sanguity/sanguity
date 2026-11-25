"""Menstrual cycle tracking in the app (#183): the setting that turns it on for a profile, what the watch receiver is
told, and what the page reads. server/cycle.py has the records themselves and the privacy rules; server/routes_cycle.py
the routes.

The setting is a settings-table row, key "_cycle" (profile scope, sealed with crypto.enc like any private setting, so
it goes with the profile when it's deleted; not in the settings registry, so it's never in the merged settings, an
export or a reset): {"on", "since", "changed", "by", "purge", "purge_at"}. Off is the default: no row. Turned off,
the row stays only until the receiver says it has done the deletion that asks for, then goes (forget_done, #935: the
page's status read and each start). Only the profile's owners change it; its owners and editors see the records;
viewers neither, and the page doesn't tell them it exists.

The app never keeps the records. The receiver keeps them (in its sealed database and archive) for a profile it's told
has tracking on, and writes them to cycle.json beside the profile's daily.json; the app reads that file when a page
asks (read()). watch_keys.publish() tells the receiver, in its secrets file, which profiles' slots have tracking on
("cycle") and the latest deletion each asked for ("cycle_purge": a new random token each time). Turning tracking
off asks for a deletion too. The receiver deletes the records from its database and rewrites every archived upload
without them, then says which deletion it did in cycle.json ("purged"), which status() shows. Until then nothing is
shown: a deletion asked for hides the records at once, whatever the receiver has done.

Uploaded Apple Health exports (#223): an export's cycle records are read only for a profile with tracking on
(jobs.py asks the importer for them then, and only then), and kept as the import's own, sealed, apart from its watch
summary: <profile_dir>/watch/cycle/<import id>.json, a list of records in cycle.json's shape (from Health Auto Export,
app "Apple Health", so a record both sent is shown once). They are never in daily.json. The page gets them with the
receiver's (status()); undoing the import takes them away (watch_store.forget_import); turning tracking off, or
asking for everything to be deleted, deletes them all at once, here in the app (drop_imported), and an import still
waiting for review that held nothing else is cancelled, and its upload with it (#539). The uploaded export itself is
written again without them then (#555: it kept them until its review was cancelled or its import undone), as the
receiver rewrites its archived uploads, and as it's uploaded while tracking is off (strip_uploads): an upload holds
cycle records only while tracking is on, and sexual activity never. One an older version kept as it came, or a backup
made by one brings back, is written so once at start-up (strip_old).
"""
import json, os, secrets, shutil

from . import crypto, cycle, db as dbm, uploads

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


def mark(db, pid):
    """How tracking stands: (on, the latest deletion asked for, when it was last turned). Turning it off or deleting
    changes it (#539: an export read meanwhile leaves out what it read, jobs._import), and so does turning it on again
    after the row went (forget_done)."""
    v = setting(db, pid)
    return bool(v.get("on")), v.get("purge"), v.get("changed")


def changed_by(db, pid):
    """The account that last changed the setting ({"id", "username"}), or None (removed since)."""
    return db.execute("SELECT u.id, u.username FROM settings s JOIN users u ON u.id=s.changed_by WHERE s.key=? "
                      "AND s.profile_id=? AND s.user_id IS NULL", (KEY, pid)).fetchone()


def _store(db, pid, v, by):
    db.execute("""INSERT INTO settings(key, profile_id, user_id, value, sealed, changed, changed_by) VALUES(?, ?, NULL, NULL, ?, ?, ?)
                  ON CONFLICT(key, IFNULL(profile_id, 0), IFNULL(user_id, 0))
                  DO UPDATE SET sealed=excluded.sealed, changed=excluded.changed, changed_by=excluded.changed_by""",
               (KEY, pid, crypto.enc(json.dumps(v, separators=(",", ":"))), dbm.now(), by))


def turn(db, pid, on, by):
    """Turn tracking on or off (inside the caller's transaction; publish after it commits). Off asks the receiver to
    delete everything it keeps, and the caller deletes what uploads brought once it commits (drop_imported). Returns
    whether anything changed."""
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
    """Ask for everything kept to be deleted, leaving tracking as it is (inside the caller's transaction; then
    drop_imported once it commits)."""
    v = setting(db, pid)
    v.update(purge=secrets.token_hex(12), purge_at=dbm.now())
    _store(db, pid, v, by)


def forget_done(db, pid=None):
    """Tracking off and the deletion it asked for done (the receiver says so in cycle.json): the setting's row goes
    (#935), so nothing the app keeps, its backups included, says the profile ever tracked its cycle. Until then the row
    holds the deletion the receiver is asked for. pid None: every profile's (at start-up). Returns how many went; the
    caller publishes after (the receiver has nothing more to do for it)."""
    q = "SELECT profile_id AS pid FROM settings WHERE key=? AND user_id IS NULL AND profile_id IS NOT NULL"
    rows = db.execute(q + ("" if pid is None else " AND profile_id=?"), (KEY,) + (() if pid is None else (pid,))).fetchall()
    gone = 0
    for r in rows:
        v = setting(db, r["pid"])
        if v.get("on") or (v.get("purge") and (read(db, r["pid"]) or {}).get("purged") != v["purge"]):
            continue
        db.execute("DELETE FROM settings WHERE key=? AND profile_id=? AND user_id IS NULL", (KEY, r["pid"]))
        gone += 1
    return gone


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
    recs = (doc.get("records") if doc.get("on") and isinstance(doc.get("records"), list) else []) + imported(db, pid)   # (#223)
    if on and not pending and recs:
        v = cycle.view(recs)
        out.update(spans=[{**s, **dict(zip(("title", "text"), cycle.words(s)))} for s in v["spans"]],
                   marks=[{**m, "title": cycle.mark_words(m)} for m in v["marks"]], count=len(recs), updated=doc.get("updated"))
    return out


# ---- cycle records an uploaded Apple Health export brought (#223)
def imported_dir(pid):
    from . import profiles as prof
    return os.path.join(prof.profile_dir(pid), "watch", "cycle")


def imported_path(pid, import_id):
    return os.path.join(imported_dir(pid), f"{int(import_id)}.json")


def save_imported(db, pid, import_id, recs):
    """Keep an import's cycle records as its own (inside the import's transaction), only while tracking is on and
    only for an import that can be undone. Returns how many were kept."""
    recs = [r for r in recs or [] if isinstance(r, dict) and r.get("kind") in cycle.LABEL]
    if import_id is not None and os.path.isfile(imported_path(pid, import_id)):   # left by a save that rolled back
        os.remove(imported_path(pid, import_id))
    if not recs or import_id is None or not is_on(db, pid):
        return 0
    os.makedirs(imported_dir(pid), exist_ok=True)
    crypto.write_file(imported_path(pid, import_id), json.dumps(recs, separators=(",", ":")).encode())
    return len(recs)


def imported(db, pid):
    """The cycle records of the profile's imports that still exist, oldest import first; [] while tracking is off."""
    d = imported_dir(pid)
    ids = sorted(int(n[:-5]) for n in os.listdir(d) if n.endswith(".json") and n[:-5].isdigit()) if os.path.isdir(d) else []
    if not ids or not is_on(db, pid):
        return []
    live = {r[0] for r in db.execute("SELECT id FROM imports WHERE profile_id=? AND id IN (SELECT value FROM json_each(?))",
                                     (pid, json.dumps(ids)))}
    out = []
    for i in ids:
        if i in live:
            try:
                v = json.loads(crypto.read_file(imported_path(pid, i)))
            except (OSError, ValueError, crypto.KeyError_):
                continue
            out += [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []
    return out


def strip_uploads(db, pid, upload_id=None, on=False):
    """Write a profile's uploaded Apple Health exports again without what isn't kept (#555): sexual activity always,
    and while tracking is off (on False) every cycle record and basal body temperature, as the receiver leaves them out
    of a phone's upload (cycle.strip): each export.xml among its ready uploads (or upload_id's alone), streamed, the
    rest of it as it was. Called when tracking is turned off or everything deleted (drop_imported), and as an upload is
    finished (routes_uploads.py, export.py), so an upload holds none while it's off. One that can't be read (not
    well-formed, entities) is left as it came: its import refuses it, and the upload goes with that (#547). Returns
    how many records were left out."""
    from .importers import apple_health
    types = cycle.HAE_NEVER | (set() if on else set(cycle.HAE) | cycle.HAE_ALSO)
    q = "SELECT id FROM uploads WHERE profile_id=? AND state='ready'" + (" AND id=?" if upload_id is not None else "") + " ORDER BY id"
    n = 0
    for (uid,) in db.execute(q, (pid,) + ((upload_id,) if upload_id is not None else ())).fetchall():
        for rel in uploads.manifest(db, pid, uid):
            if rel.rsplit("/", 1)[-1].lower() != "export.xml":
                continue
            try:
                n += uploads.rewrite(db, pid, uid, rel, lambda pieces, write: apple_health.without(_health_data(pieces), write, types))
            except (ValueError, uploads.UploadError, crypto.KeyError_, OSError):
                continue
    return n


STRIPPED = "uploads_health_stripped"   # meta: how far strip_old has got, "<the highest upload id then>:<done to>"


def strip_old(db):
    """At start-up (main.py), once for each upload: strip_uploads over the uploads an older version kept as they came
    (#555: before it, an Apple Health export.xml was kept with its sexual activity, and its cycle records whatever
    tracking was), or that a backup made by one brings back: sexual activity always, cycle records and basal body
    temperature where tracking is off. The uploads there at the first start that has this (up to the highest id given
    then) are each read once, in order, the meta row saying how far it got, so a stop part-way goes on from there and a
    later start reads none; an upload finished since was written so as it was finished. The row is in the database the
    uploads are, so a backup restored from before it has none, and its uploads are read at the next start. Returns how
    many records were left out."""
    try:
        upto, done = (int(x) for x in (dbm.meta_get(db, STRIPPED) or "").split(":"))
    except ValueError:   # (none yet: the first start)
        upto, done = db.execute("SELECT COALESCE(MAX(id), 0) FROM uploads").fetchone()[0], 0
        dbm.meta_set(db, STRIPPED, f"{upto}:0")
    n = 0
    for r in db.execute("SELECT id, profile_id FROM uploads WHERE state='ready' AND id>? AND id<=? ORDER BY id",
                        (done, upto)).fetchall():
        n += strip_uploads(db, r["profile_id"], r["id"], on=is_on(db, r["profile_id"]))
        dbm.meta_set(db, STRIPPED, f"{upto}:{r['id']}")
    if done < upto:
        dbm.meta_set(db, STRIPPED, f"{upto}:{upto}")
    return n


def _health_data(pieces):
    """An export.xml's pieces, refused (ValueError) unless it's an Apple Health export: <HealthData in its head."""
    head, it = bytearray(), iter(pieces)
    for piece in it:
        head += piece
        if b"<HealthData" in head:
            yield bytes(head)
            yield from it
            return
        if len(head) > 256 * 1024:   # (apple_health.HEAD_BYTES: Apple's DTD is ~10 KB)
            break
    raise ValueError("not an Apple Health export")


def drop_imported(db, pid, who=None, ip=None):
    """Delete every cycle record uploaded exports brought this profile: the imports' own, and those in an import's
    preview still waiting to be confirmed (its sealed file is written again without them; one that held nothing else
    is cancelled, its upload removed, as who would with Cancel, #539). Called once the change has committed
    (routes_cycle.py), since a preview read again holds its lock while it waits to write (jobs.freshen): each preview
    is written again under that lock, so neither writes back what the other took out, and not for a job saved or
    cancelled meanwhile (jobs.rewrite_preview, #526). An export still being read asks again under the same lock
    before its preview is written (jobs._import), so one that gets there meanwhile is seen here (#539). Then every
    Apple Health export the profile's uploads hold is written again without them (strip_uploads, #555)."""
    from . import export, jobs
    shutil.rmtree(imported_dir(pid), ignore_errors=True)
    export.strip_uploads(db, pid, cycle=False)   # and an export of Sanguity's uploaded here, its cycle/cycle.json (#918)
    for (jid,) in db.execute("SELECT id FROM jobs WHERE profile_id=? AND (state='preview' OR state='running' AND stage IS NOT 'saving')",
                             (pid,)).fetchall():
        with jobs.preview_lock(jid):
            r = db.execute("SELECT summary FROM jobs WHERE id=? AND state='preview'", (jid,)).fetchone()
            path = jobs.preview_path(pid, jid)
            try:
                held = json.loads(crypto.read_file(path)) if r else {}
                report = json.loads(crypto.dec(r["summary"])) if r and r["summary"] else None
            except (OSError, ValueError, crypto.KeyError_):
                continue
            if not r:
                continue
            had = isinstance(held.get("watch"), dict) and held["watch"].pop("cycle", None) is not None
            if jobs._holds_nothing(held) and not jobs._first_pull(db, jid):   # an upload of cycle records only (#539),
                jobs._cancel(db, pid, jid, who, ip)   # or one left holding nothing whatever it held (#542)
            elif had and jobs.rewrite_preview(db, jid, path, held):
                if isinstance(report, dict) and (report.get("counts") or {}).pop("cycle", None) is not None:   # nor counted
                    # and read again without them (#534): what's new then
                    db.execute("UPDATE jobs SET summary=? WHERE id=? AND state='preview'", (crypto.enc(json.dumps({**report, "stamp": None})), jid))
                    jobs.freshen_held(db, pid, jid)
    strip_uploads(db, pid)   # and the exports uploaded, written again without them (#555)


RESTORED = "cycle_restored"   # meta, sealed: the profiles a restore brought back cycle records of that it doesn't keep (#964)


def restored(db):
    """(#964) A restore keeps each profile's tracking as it is today (backup._keep_access), and notes, sealed (nothing
    plain may say which profiles track their cycle, #935), the profiles whose cycle records the backup holds that
    today's tracking doesn't keep: off now, or a deletion asked for since. Those records go here as turning tracking
    off deletes them (drop_imported: an uploaded export's, every upload's, a review's), then the note. Run by the
    restore once its files are back, and at each start (a restore a stop cut short). Returns how many profiles."""
    v = dbm.meta_get(db, RESTORED)
    if not v:
        return 0
    try:
        pids = [int(p) for p in json.loads(crypto.dec(v))]
    except (ValueError, TypeError, crypto.KeyError_):
        pids = []
    for pid in pids:
        if db.execute("SELECT 1 FROM profiles WHERE id=?", (pid,)).fetchone():
            drop_imported(db, pid)
    db.execute("DELETE FROM meta WHERE k=?", (RESTORED,))
    return len(pids)


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
