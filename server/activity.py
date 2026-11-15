"""The activity log: who did what, and when, on each profile; and changes to accounts.

Profile events (profile_id set), readable by the profile's owners:
  view          opened the records (once per session)            document   opened a document (once per session each)
  create        created the profile                              rename     {"from", "to"}
  share         {"username", "role", "from"}: gave access or changed a role ("from" is the old role, if any)
  unshare       {"username"}: removed someone's access, or left (the actor is then that person)
  import        {"documents"}: records loaded with server.manage    reload     records reloaded from the setup build
  export        downloaded everything in the profile as a zip
Account events (no profile), readable by admins:
  setup         the first account                                password   changed their own password
  account.create / account.disable / account.enable / account.admin / account.unadmin / account.reset   {"username"}
  2fa.on / 2fa.off / 2fa.codes (new recovery codes) / 2fa.recovery (signed in with one, {"left"})
  2fa.reset {"username"}: an admin turned someone's off            settings.require_2fa {"on"}
  profile.delete {"name", "members"}: a profile and all its records deleted (its own log goes with it)
  account.remove {"username", "handed_over"}: an account deleted; profiles it alone owned went to someone else
  profile.repair {"name", "username"}: an admin gave a profile with no active owner a new one (also logged on the
                profile itself, as a share with "repair": true)

Entries keep the actor's username as it was, so they read the same after an account is gone. A profile's entries
go with the profile. Entries older than ACTIVITY_DAYS are pruned (at most once a day, when something is logged).
"""
import json

from . import config, db as dbm

PROFILE_ACTIONS = ("view", "document", "create", "rename", "share", "unshare", "import", "reload", "export")
ACCOUNT_ACTIONS = ("setup", "password", "account.create", "account.disable", "account.enable", "account.admin",
                   "account.unadmin", "account.reset", "2fa.on", "2fa.off", "2fa.codes", "2fa.recovery", "2fa.reset",
                   "settings.require_2fa", "profile.delete", "account.remove", "profile.repair")
COMMAND_LINE, SETUP_BUILD = "command line", "setup build"
SYSTEM = (COMMAND_LINE, SETUP_BUILD)


def log(db, action, who=None, profile_id=None, detail=None, ip=None, session_id=None, actor=None):
    """Record an event. who is the signed-in account (a session row) or None for the system; actor names it
    otherwise (COMMAND_LINE, SETUP_BUILD)."""
    db.execute("INSERT INTO activity(at, profile_id, actor_id, actor, action, detail, ip, session_id) VALUES(?,?,?,?,?,?,?,?)",
               (dbm.now(), profile_id, who["id"] if who is not None else None,
                who["username"] if who is not None else (actor or SETUP_BUILD), action,
                json.dumps(detail, separators=(",", ":")) if detail else None, ip, session_id))
    _prune_now_and_then(db)


def once(db, s, profile_id, action, ip, detail=None, key=None):
    """Log action for this session and profile unless it's already logged (key tells documents apart)."""
    if db.execute("SELECT 1 FROM activity WHERE session_id=? AND profile_id=? AND action=? AND IFNULL(json_extract(detail, '$.key'), '')=?",
                  (s["sid"], profile_id, action, key or "")).fetchone():
        return
    log(db, action, s, profile_id, {**(detail or {}), **({"key": key} if key else {})} or None, ip, s["sid"])


def _prune_now_and_then(db):
    now = dbm.now()
    last = int(dbm.meta_get(db, "activity_pruned") or 0)
    if now - last < 86400:
        return
    db.execute("DELETE FROM activity WHERE at < ?", (now - config.ACTIVITY_DAYS * 86400,))
    dbm.meta_set(db, "activity_pruned", str(now))


def _rows(db, where, args, before, limit):
    q = f"SELECT id, at, profile_id, actor_id, actor, action, detail, ip FROM activity WHERE {where}"
    if before:
        q, args = q + " AND id < ?", (*args, before)
    out = []
    for r in db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, min(max(limit, 1), 500))):
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else {}
        d["detail"].pop("key", None)
        d["actor_removed"] = d["actor_id"] is None and d["actor"] not in SYSTEM   # the account was removed since
        out.append(d)
    return out


def for_profile(db, pid, actor_id=None, action=None, before=None, limit=100):
    where, args = "profile_id=?", (pid,)
    if actor_id is not None:
        where, args = where + " AND actor_id=?", (*args, actor_id)
    if action:
        where, args = where + " AND action=?", (*args, action)
    return _rows(db, where, args, before, limit)


def for_accounts(db, before=None, limit=100):
    return _rows(db, "profile_id IS NULL", (), before, limit)
