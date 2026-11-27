"""The watch receiver's secrets, one per profile (#46), so each person's phone uploads to their own profile.

The phone app signs every upload with a secret (HMAC-SHA256 of the body, watch/receiver.py); the receiver finds the
profile whose secret made the signature and folds the upload into that profile's own summary. A profile's owners and
editors make its secret on the Sources page (Phone and watch), and make a new one ("rotate") when a phone is lost or
replaced; the secret is shown once, when it's made. A secret goes, for good, when the person who made it can no longer
change the profile (end_lost, #929, as their share links and calendar feeds do, #590): their phone stops uploading.

Where things are kept:
  the settings table, key "_watch.receiver" (profile scope, sealed with crypto.enc like any private setting, so it
      goes with the profile when it's deleted and is re-encrypted with a new key; not in the settings registry, so
      it's never in the merged settings, an export, or a reset): {"secret", "slot", "created", "by"}
  config.WATCH_KEYS (compose: WATCH_DIR/keys/receiver-secrets.json), sealed with crypto.write_file: every profile's
      secret and slot, rewritten by publish() at start-up and after each change; the receiver reads it (and notices
      when it changes)
  the summaries: the receiver writes slot "" to its own top folder, as before (public/daily.json, which
      config.WATCH_FILE names), and any other slot to profiles/<slot>/ (public/profiles/<slot>/daily.json)
  the meta row "watch_deleted": the slots of deleted profiles ({slot: token}), in the file as "deleted", so the
      receiver deletes what each kept (forget, #588); a profile's id, and so its slot, never comes again
  in the file as "slots": every slot a profile reads (read_slots, #593); the receiver deletes, after a grace period,
      a slot it keeps that none names and no secret uploads to (one a profile deleted before #588 left behind)

Installs from before #46 had one secret (auth/watch_secret) and one summary, read by the first profile (the one whose
watch_file is config.WATCH_FILE). That keeps working: the receiver accepts the old secret for slot "" until the first
profile makes a secret of its own here ("legacy": false in the file), and that profile keeps slot "", so its watch
history carries on. Every other profile gets a slot of its own, its id.
"""
import json, os, secrets
from urllib.parse import urlsplit

from . import config, crypto, db as dbm, watch_store

KEY = "_watch.receiver"   # a settings-table row, not a registry setting (settings.py skips keys it doesn't define)


def _row(db, pid):
    r = db.execute("SELECT sealed FROM settings WHERE key=? AND profile_id=? AND user_id IS NULL", (KEY, pid)).fetchone()
    if r is None or r["sealed"] is None:
        return None
    try:
        v = json.loads(crypto.dec(r["sealed"]))
    except (ValueError, TypeError):
        return None
    return v if isinstance(v, dict) and v.get("secret") else None


def _same(a, b):
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def first_profile(db):
    """The profile that reads the receiver's own summary (config.WATCH_FILE), lowest id first; None if none does."""
    for r in db.execute("SELECT id, watch_file FROM profiles ORDER BY id"):
        if _same(r["watch_file"], config.WATCH_FILE):
            return r["id"]
    return None


def summary_path(slot):
    """Where the app reads a slot's summary (the receiver writes it under its public/ folder)."""
    if slot == "":
        return config.WATCH_FILE
    return os.path.join(os.path.dirname(config.WATCH_FILE), "profiles", slot, "daily.json")


def _slot_for(db, pid):
    row = _row(db, pid)
    if row is not None:
        return row["slot"]
    return "" if first_profile(db) == pid else str(int(pid))


RETIRED = "watch_legacy_retired"   # meta: the install's single secret was replaced, for good


def legacy_in_use(db):
    """Whether the receiver still takes the install's single secret (auth/watch_secret), for slot "": until the
    profile in that slot (the first) makes a secret of its own. Then never again, even if that profile is deleted."""
    return not dbm.meta_get(db, RETIRED)


def retire_unused(db):
    """(#1103) A fresh install's first profile doesn't read the receiver's own summary (slot "", #1101), so no profile
    does and the install's single secret would send a phone's uploads where nothing reads them: it's retired, and the
    profile has a phone once it makes a secret of its own (it was "set up, waiting for its first upload", and Sources
    spoke of the server's original secret). While a profile reads that summary, it's kept."""
    if legacy_in_use(db) and first_profile(db) is None:
        dbm.meta_set(db, RETIRED, str(dbm.now()))


def publish(db):
    """Write the receiver's secrets file from the database. Returns True, or False when its folder isn't there
    (an install whose compose file doesn't mount WATCH_DIR/keys yet), saying so in the log."""
    profiles = []
    for r in db.execute("SELECT s.profile_id AS pid FROM settings s JOIN profiles p ON p.id=s.profile_id "
                        "WHERE s.key=? AND s.user_id IS NULL ORDER BY s.profile_id", (KEY,)).fetchall():
        row = _row(db, r["pid"])
        if row:
            profiles.append({"profile": r["pid"], "slot": row["slot"], "secret": row["secret"]})
    from . import cycle_tracking   # which profiles turned cycle tracking on, and deletions asked for (#183)
    doc = {"version": 1, "legacy": legacy_in_use(db), "profiles": profiles, **cycle_tracking.keys_doc(db),
           "deleted": deleted(db), "slots": read_slots(db)}
    folder = os.path.dirname(config.WATCH_KEYS)
    if not os.path.isdir(folder):
        if profiles:
            print(f"The watch receiver's secrets can't be written: there's no folder {folder} (in compose, WATCH_DIR/keys "
                  "mounted at /srv/watch-keys). Phones set up on the Sources page won't be able to upload until it is.",
                  flush=True)
        return False
    crypto.write_file(config.WATCH_KEYS, json.dumps(doc, separators=(",", ":")).encode())
    return True


def read_slots(db):
    """Every receiver slot a profile reads, or would with a phone set up (its own, or slot "" for the first), sorted:
    in the file as "slots", so the receiver deletes, after a grace period, a slot none names (#593)."""
    pub = os.path.abspath(os.path.dirname(config.WATCH_FILE))
    out = set()
    for r in db.execute("SELECT id, watch_file FROM profiles").fetchall():
        out.add(_slot_for(db, r["id"]))
        wf = r["watch_file"]
        if _same(wf, config.WATCH_FILE):
            out.add("")
        elif wf:
            parts = os.path.relpath(os.path.abspath(wf), pub).split(os.sep)
            if len(parts) == 3 and parts[0] == "profiles" and parts[1].isdigit() and parts[2] == "daily.json":
                out.add(parts[1])
    return sorted(out, key=lambda s: (s != "", int(s) if s else 0))


DELETED = "watch_deleted"   # meta: {slot: token}, the slots of deleted profiles, for the receiver to delete (#588)


def deleted(db):
    try:
        v = json.loads(dbm.meta_get(db, DELETED) or "{}")
    except ValueError:
        v = {}
    return v if isinstance(v, dict) else {}


def forget(db, pid):
    """A profile is being deleted (call inside its transaction, publish() after): ask the receiver to delete what it
    keeps for the profile's slot (watch/receiver.py slot_sweep), so no profile made later reads it. Not while another
    profile reads the same slot (the receiver's own summary, slot "", can be several profiles' watch file): what's
    kept stays theirs. For slot "", the install's single secret stops working too. Returns the slot, or None."""
    slot = _slot_for(db, pid)
    path = summary_path(slot)
    for r in db.execute("SELECT id, watch_file FROM profiles WHERE id != ?", (pid,)).fetchall():
        if _same(r["watch_file"], path) or (_row(db, r["id"]) or {}).get("slot") == slot:
            return None
    asked = deleted(db)
    asked[slot] = f"{dbm.now()}-{secrets.token_hex(4)}"   # a new token: slot "" is deleted again
    dbm.meta_set(db, DELETED, json.dumps(asked, separators=(",", ":")))
    if slot == "":
        dbm.meta_set(db, RETIRED, str(dbm.now()))
    return slot


def leftover_ids(db):
    """The highest profile id a folder on disk or a receiver slot is named by (0: none). A profile deleted before
    schema version 39 (#588) may have left its receiver slot, which the next profile given its id would read."""
    top = max((int(s) for s in deleted(db) if s.isdigit()), default=0)
    dirs = [os.path.join(config.DATA_DIR, "profiles"), os.path.join(os.path.dirname(config.WATCH_FILE), "profiles")]
    if config.WATCH_STORE:
        dirs.append(os.path.join(config.WATCH_STORE, "profiles"))
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            n = n[len(".deleting-"):] if n.startswith(".deleting-") else n
            if n.isdigit():
                top = max(top, int(n))
    return top


def ready():
    return os.path.isdir(os.path.dirname(config.WATCH_KEYS))


def addresses(request_host="", origins=None, url=None):
    """The upload addresses to give the phone app. origins, url: other values than PUBLIC_ORIGINS' and
    WATCH_UPLOAD_URL's in force, for what a change to one would give (General, #1000, #1003)."""
    if (url := config.setting("WATCH_UPLOAD_URL") if url is None else url):
        return [url]
    hosts = []
    for o in sorted(config.setting("PUBLIC_ORIGINS") if origins is None else origins):
        h = urlsplit(o).hostname
        if h and h not in hosts:
            hosts.append(h)
    if not hosts and request_host:
        h = urlsplit("//" + request_host).hostname
        if h:
            hosts.append(h)
    return [f"http://{'[' + h + ']' if ':' in h else h}:{config.setting('RECEIVER_PORT')}/ingest" for h in hosts]


def with_secret(db):
    """How many profiles' phones upload with a secret: their own, or the install's single one from before #46 (the
    first profile's, once a phone has sent it something). Each needs the new address when the upload address changes
    (General, #1000, #1003)."""
    n = sum(1 for r in db.execute("SELECT profile_id FROM settings WHERE key=? AND profile_id IS NOT NULL AND user_id IS NULL",
                                  (KEY,)) if _row(db, r["profile_id"]) is not None)
    first = first_profile(db)
    legacy = first is not None and _row(db, first) is None and legacy_in_use(db) and last_upload(None, True) is not None
    return n + legacy


def mask(secret):
    return "•" * 12 + secret[-4:]


def status(db, pid, request_host=""):
    """What the Sources page's Phone and watch shows: whether this profile has a secret (and its last characters), whether it's the
    install's old single secret, the upload addresses, and whether the receiver can be told about secrets."""
    row = _row(db, pid)
    legacy = row is None and first_profile(db) == pid and legacy_in_use(db)
    return {"has_secret": row is not None, "masked": mask(row["secret"]) if row else None,
            "created": row.get("created") if row else None, "legacy": legacy, "addresses": addresses(request_host),
            "ready": ready(), "last": last_upload(row, legacy)}


def last_upload(row, legacy):
    """When the phone last sent this profile something (the receiver rewrites the slot's summary on each upload),
    for the Sources page's overview (#166); None before its first upload."""
    path = summary_path(row.get("slot", "")) if row else config.WATCH_FILE if legacy else None
    try:
        return int(os.path.getmtime(path)) if path else None
    except OSError:
        return None


def maker(db, pid):
    """The account that made the profile's secret (its id), or None."""
    row = _row(db, pid)
    return row.get("by") if row else None


def end_lost(db):
    """Take away every profile's secret whose maker is no longer an active owner or editor of it (#929: removed, made
    a viewer, their account disabled or removed), so the phone they set up with it can't upload: given access again,
    they'd find it working, as a share link would (accounts.end_lost_access). Inside the caller's transaction; the
    receiver is told once it commits. Each is in the profile's activity log. Returns the profiles whose secret went."""
    from . import activity
    gone = []
    for r in db.execute("SELECT profile_id FROM settings WHERE key=? AND user_id IS NULL AND profile_id IS NOT NULL",
                        (KEY,)).fetchall():
        pid, by = r["profile_id"], maker(db, r["profile_id"])
        if by is None or db.execute("""SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id WHERE m.user_id=?
                                       AND m.profile_id=? AND m.role IN ('owner', 'editor') AND u.disabled=0""", (by, pid)).fetchone():
            continue
        name = db.execute("SELECT username FROM users WHERE id=?", (by,)).fetchone()
        db.execute("DELETE FROM settings WHERE key=? AND profile_id=? AND user_id IS NULL", (KEY, pid))
        activity.log(db, "watch.secret", None, pid, {"ended": True, "maker": name["username"] if name else None},
                     actor=activity.ACCESS_ENDED)
        gone.append(pid)
    if gone:
        dbm.after_commit(db, lambda: publish(db))
    return gone


def rotate(db, pid, by):
    """Make a new secret for the profile (its first, or in place of the one it had). Inside the caller's
    transaction; call publish() after it commits. Returns the secret, the only time it's given out."""
    slot = _slot_for(db, pid)
    secret = secrets.token_urlsafe(32)
    v = json.dumps({"secret": secret, "slot": slot, "created": dbm.now(), "by": by}, separators=(",", ":"))
    db.execute("""INSERT INTO settings(key, profile_id, user_id, value, sealed, changed, changed_by) VALUES(?, ?, NULL, NULL, ?, ?, ?)
                  ON CONFLICT(key, IFNULL(profile_id, 0), IFNULL(user_id, 0))
                  DO UPDATE SET sealed=excluded.sealed, changed=excluded.changed, changed_by=excluded.changed_by""",
               (KEY, pid, crypto.enc(v), dbm.now(), by))
    if slot == "":
        dbm.meta_set(db, RETIRED, str(dbm.now()))
    target = summary_path(slot)
    current = db.execute("SELECT watch_file FROM profiles WHERE id=?", (pid,)).fetchone()["watch_file"]
    if not _same(current, target):
        # the receiver's summary becomes the profile's watch file; Apple Health imports move beside it (watch_store)
        was_own = _same(current, watch_store.own_path(pid))
        watch_store._migrate_profile(db, pid)   # imports from before #24 kept as a layer first, so nothing is lost
        db.execute("UPDATE profiles SET watch_file=? WHERE id=?", (target, pid))
        watch_store.rebuild(db, pid)
        if was_own:
            try:
                os.remove(watch_store.own_path(pid))
            except FileNotFoundError:
                pass
    return secret
