"""The watch receiver's secrets, one per profile (#46), so each person's phone uploads to their own profile.

The phone app signs every upload with a secret (HMAC-SHA256 of the body, watch/receiver.py); the receiver finds the
profile whose secret made the signature and folds the upload into that profile's own summary. A profile's owners and
editors make its secret on the Sources page (Phone and watch), and make a new one ("rotate") when a phone is lost or
replaced; the secret is shown once, when it's made.

Where things are kept:
  the settings table, key "_watch.receiver" (profile scope, sealed with crypto.enc like any private setting, so it
      goes with the profile when it's deleted and is re-encrypted with a new key; not in the settings registry, so
      it's never in the merged settings, an export, or a reset): {"secret", "slot", "created", "by"}
  config.WATCH_KEYS (compose: WATCH_DIR/keys/receiver-secrets.json), sealed with crypto.write_file: every profile's
      secret and slot, rewritten by publish() at start-up and after each change; the receiver reads it (and notices
      when it changes)
  the summaries: the receiver writes slot "" to its own top folder, as before (public/daily.json, which
      config.WATCH_FILE names), and any other slot to profiles/<slot>/ (public/profiles/<slot>/daily.json)

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


def publish(db):
    """Write the receiver's secrets file from the database. Returns True, or False when its folder isn't there
    (an install whose compose file doesn't mount WATCH_DIR/keys yet), saying so in the log."""
    profiles = []
    for r in db.execute("SELECT s.profile_id AS pid FROM settings s JOIN profiles p ON p.id=s.profile_id "
                        "WHERE s.key=? AND s.user_id IS NULL ORDER BY s.profile_id", (KEY,)).fetchall():
        row = _row(db, r["pid"])
        if row:
            profiles.append({"profile": r["pid"], "slot": row["slot"], "secret": row["secret"]})
    doc = {"version": 1, "legacy": legacy_in_use(db), "profiles": profiles}
    folder = os.path.dirname(config.WATCH_KEYS)
    if not os.path.isdir(folder):
        if profiles:
            print(f"The watch receiver's secrets can't be written: there's no folder {folder} (in compose, WATCH_DIR/keys "
                  "mounted at /srv/watch-keys). Phones set up on the Sources page won't be able to upload until it is.",
                  flush=True)
        return False
    crypto.write_file(config.WATCH_KEYS, json.dumps(doc, separators=(",", ":")).encode())
    return True


def ready():
    return os.path.isdir(os.path.dirname(config.WATCH_KEYS))


def addresses(request_host=""):
    """The upload addresses to give the phone app."""
    if config.WATCH_UPLOAD_URL:
        return [config.WATCH_UPLOAD_URL]
    hosts = []
    for o in sorted(config.PUBLIC_ORIGINS):
        h = urlsplit(o).hostname
        if h and h not in hosts:
            hosts.append(h)
    if not hosts and request_host:
        h = urlsplit("//" + request_host).hostname
        if h:
            hosts.append(h)
    return [f"http://{'[' + h + ']' if ':' in h else h}:{config.RECEIVER_PORT}/ingest" for h in hosts]


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
