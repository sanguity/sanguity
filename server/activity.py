"""The activity log: who did what, and when, on each profile; and changes to accounts.

Profile events (profile_id set), readable by the profile's owners:
  view          opened the records (once per session)            document   opened a document (once per session each)
  create        created the profile                              rename     {"from", "to"}
  share         {"username", "role", "from"}: gave access or changed a role ("from" is the old role, if any)
  unshare       {"username"}: removed someone's access, or left (the actor is then that person)
  import        {"documents"}: records loaded with server.manage; {"importer", "counts", "job"}: an upload imported in
                the browser (jobs.py), with {"labs": {"matched", "remembered"}} when lab names were matched (labmap.py)                             reload     records reloaded from the setup build
  export        downloaded everything in the profile as a zip; {"cycle": true} with the cycle records ticked in (#183);
                {"format": "fhir", "parts": [...]} as FHIR R4, with the kinds of records ticked (#205)
  upload        {"upload", "files", "bytes"}: files uploaded on the import page (uploads.py)
  discard       {"importer", "job", "upload"}: an import cancelled at its preview (jobs.py); an import confirmed
                there is logged as import, with {"skipped", "kept"} duplicates
  undo          {"import", "origin", "importer", "label", "counts", "uploads"}: an import undone, with everything it brought
                (history.py)
  edit          {"action", "title", "to"?, "undone"?}: a correction made to an event, or undone (edits.py); action
                "same" / "not_same": two lab results from different sources linked as one, or marked apart
                (crossmatch.py, title: the test)
  settings      {"setting", "label"} a profile setting changed, {"setting", "label", "reset": true} put back to its
                default, or {"section", "label", "settings", "reset": true} a section reset (settings.py; never values)
  outside       {"host", "purpose"}: Sanguity contacted another server for this profile (outside.py, through net.py):
                a SMART Health Link and a Health Card issuer's keys after the person confirmed (shl.py), or a
                connected source's hosts ("Connected source: sign-in", "sync", "disconnect"; connections.py, sync.py)
  connect       {"source", "host", "reconnect"?}: connected a source by signing in to it (connections.py, #32)
  disconnect    {"source", "revoked", "removed"}: disconnected one, keeping or removing the records it brought;
                {"source", "ended": true}: the vendor ended access, so it needs a reconnect
  calendar      {"change": "create" | "rotate" | "change" | "revoke", "details"?, "labs"?, "username"?}: someone's calendar
                feed of the profile (calendar.py, #51); username when an owner revoked someone else's. Fetches aren't logged
  journal       {"action", "kind"}: a journal entry added, changed or deleted (journal.py; action is "add", "change"
                or "delete", kind "note", "symptom", "side_effect" or "mood" (#161); never what it says)
                {"action"}: a spot renamed, merged into another or taken off a photo (#1139; "spot.rename", "spot.merge",
                "spot.untag"; never its name)
  summary       {"sections", "since", "for", "pages", "questions"}: made a visit summary PDF (summary.py, #48; the
                sections and dates only, never the questions or any result)
  emergency     {"file": "pdf" | "png", "paper"?, "wallet"?}: made the emergency card's PDF or lock-screen image
                (emergency.py, #1126; never what's on it)
  watch.secret  {"ended", "maker"} (actor ACCESS_ENDED): the secret its maker made went with their access (#929)
  watch.secret  {"rotated"}: made the profile's watch receiver secret, or a new one in place of the old (#46; never
                the secret)
  cycle         {"change": "on" | "off" | "delete"}: an owner turned menstrual cycle tracking on or off (off deletes
                what was kept), or deleted what was kept (cycle_tracking.py, #183; never a record)
  source       {"import", "label", "named"}: an import given its own name, or its name taken away (named false;
                records.rename_import, #38; never the name)
  medication    {"change": "reminder.add" | "reminder.change" | "reminder.on" | "reminder.off" | "reminder.pause" |
                "reminder.resume" | "reminder.delete", "times"?}:
                a medication reminder set, changed, turned on or off, paused or resumed, or removed (med_reminders.py,
                #162; never which medication); {"change": "dose.add" | "dose.change" | "dose.delete", "status"}: a dose logged, corrected
                or removed (med_log.py, #235; never the medication or the note); {"change": "own.add" | "own.back" |
                "own.change" | "own.delete" | "listed.change" | "listed.back" | "listed.hide" | "listed.show" |
                "taking.stop" | "taking.again" | "taking.back"}: a medication of the profile's own added, put back,
                changed or deleted, or one of the records' changed, put back as listed, hidden or shown again
                (medications.py, #1091; never which medication); either marked stopped, started again, or that undone
                (#1161)
  family        {"change": "add" | "back" | "change" | "delete" | "hide" | "show"}: a family history entry of the
                profile's own added, put back, changed or deleted, or one of the records' hidden or shown again
                (family.py, #1134; never the relative or a condition)
  labmap        {"change": "add" | "keep" | "change" | "check" | "remove" | "undo", "matches", "moved"?}: lab-name
                matches of the profile's own made, kept as their own test, changed, kept (one to check), removed or
                undone by an owner or editor, with how many saved results were re-filed (labmap.py, #1097; never names)
  notify        {"kind": "results" | "doses", "on"}: someone turned a kind of notification on or off for the profile
                (notify.py, #1130)
  insurance     {"change": "add" | "change" | "delete" | "photo" | "photo.delete" | "read", "side"?}: an insurance
                plan added, changed or removed, a photo of its card's front or back put on or taken off, or a card's
                photo read by text recognition for suggestions (insurance.py, #1137, #1168; never the plan's name,
                member ID or numbers, nor what was read)
  link          {"change": "create" | "revoke" | "view", "link", "label"?, ...}: a share link made or revoked by an owner
                (with its sections, number of labs, dates and hours), or opened by whoever holds it: actor SHARE_LINK,
                with the viewer's address (share_links.py, #47; never the token)
Account events (no profile), readable by admins:
  setup         the first account                                password   changed their own password
  account.create / account.disable / account.enable / account.admin / account.unadmin / account.reset   {"username"}
  2fa.on / 2fa.off / 2fa.codes (new recovery codes) / 2fa.recovery (signed in with one, {"left"})
  2fa.reset {"username"}: an admin turned someone's off            settings.require_2fa {"on"}
  2fa.stopped {"wrong"}: the account's authenticator codes stopped working after that many wrong ones in a row (#928)
  profile.delete {"name", "members"}: a profile and all its records deleted (its own log goes with it)
  account.remove {"username", "handed_over"}: an account deleted; profiles it alone owned went to someone else
  profile.repair {"name", "username"}: an admin gave a profile with no active owner a new one (also logged on the
                profile itself, as a share with "repair": true)
  labmap.change {"name", "importer", "from", "to", ("kept": true)} / labmap.remove {"name", "importer", "target"} /
                labmap.undo {"name", "importer", "target"}: an admin changed (or kept, a match to check, #575) or
                removed a lab name match for every profile, or undid that from a profile's card (#1114; labmap.py;
                target null: kept as a series of its own; an undo's: as it was put back)
  connect.source {"name", "issuer", "change"}: an admin set up, changed or removed a connected source's settings
                (connections.py; change is "add", "edit" or "remove")
  outside       {"host", "purpose"}: Sanguity contacted a vendor with no profile involved (an admin's "Test this
                connection", connections.py)
  settings.user / settings.install   like a profile's settings entry: someone's own setting, or the install's (admin,
                or actor COMMAND_LINE: server.manage config reset); an install setting's has its "from" and "to" values,
                and a section reset's "changes", [{"setting", "label", "from", "to"}] (#1006; none is a secret)
  server.restart {"changes": [{"setting", "label", "from", "to"}], ("fell_back": why)}: an admin's Restart now on General,
                with what the next start tries; or, by actor START_UP, a start that fell back to the settings before
                (server_settings.py, #1121). Saving one for the next start is a settings.install entry
  backup.create {"name", "size", "removed"} / backup.check {"name", "ok"} / backup.fail {"error"} / backup.schedule
                {"schedule", "keep", "from"}: a backup made (Back up now, or actor SCHEDULED_BACKUP), checked by
                restoring a copy, failing, or the schedule changed (backup_jobs.py, #200)
  backup.replaced {"name", ("days")}: what a restore replaced (DATA_DIR/before-restore-<time>/) deleted, by an admin's
                Delete now, or by actor TIME_LIMIT days after the restore (backup_jobs.sweep_replaced, #595)
  directory.update {"origin", "read": {list: entries}, "failed": {list: why}}: the provider directory's lists read
                by an admin's Add the lists / Update now, or by actor WEEKLY_DIRECTORY (directory/update.py, #992)
  notify.test {"ok", "why"?} / notify.token {"change": "set" | "remove"} / notify.topic: a test notification to one's
                own topic (an admin's Test, or anyone's), the server's access token saved or removed (never the
                token), someone's own topic made new (never the topic); notify.fail {"kind", "tries", "why"}: a
                notification that didn't go through after its tries, by actor NOTIFICATIONS (notify.py, #1130; never
                what it said)
  records.rekey {"left": [what]}: records saved before #1022 that start-up couldn't key as they are now, left as they
                were, by actor START_UP (records.rekey_stamped, #1044)
  records.copies {"events", "results", "entries", "marks"}: the copies #1022's bug made, keyed alike by
                records.rekey_stamped, made one record each by actor START_UP: records, lab results (#1082) and list
                entries removed, Costs marks moved (records.merge_copies, #1068)

Entries keep the actor's username as it was, so they read the same after an account is gone. A profile's entries
go with the profile. Entries older than ACTIVITY_DAYS are pruned (at most once a day, when something is logged).
"""
import json

from . import config, crypto, db as dbm

PROFILE_ACTIONS = ("view", "document", "create", "rename", "share", "unshare", "import", "reload", "export", "upload", "discard", "undo", "edit", "outside",
                   "settings", "connect", "disconnect", "calendar", "journal", "summary", "emergency", "watch.secret", "source", "link", "cycle",
                   "medication", "labmap", "family", "notify", "insurance", "followup", "request")
ACCOUNT_ACTIONS = ("setup", "password", "account.create", "account.disable", "account.enable", "account.admin",
                   "account.unadmin", "account.reset", "2fa.on", "2fa.off", "2fa.codes", "2fa.recovery", "2fa.reset", "2fa.stopped",
                   "settings.require_2fa", "profile.delete", "account.remove", "profile.repair", "labmap.change",
                   "labmap.remove", "labmap.undo", "settings.user", "settings.install", "connect.source", "outside",
                   "backup.create", "backup.check", "backup.fail", "backup.schedule", "backup.replaced", "directory.update",
                   "records.rekey", "records.copies", "server.restart", "notify.test", "notify.fail", "notify.token", "notify.topic",
                   "ocr.language")
COMMAND_LINE, SETUP_BUILD = "command line", "setup build"
SCHEDULED_SYNC = "scheduled sync"   # a connected source's pull on its schedule (sync.py)
SHARE_LINK = "share link"           # whoever holds a share link, opening it (share_links.py)
SCHEDULED_BACKUP = "scheduled backup"   # a backup on the Admin page's schedule (backup_jobs.py, #200)
TIME_LIMIT = "time limit"   # something deleted when its time is up: what a restore replaced (backup_jobs.py, #595)
ACCESS_ENDED = "access ended"   # what ends with its maker's access: a phone upload secret (watch_keys.end_lost, #929)
WEEKLY_DIRECTORY = "weekly directory update"   # the provider directory's lists read again (directory/update.py, #992)
START_UP = "start-up"   # what the app does as it starts (records.rekey_stamped, #1044)
NOTIFICATIONS = "notifications"   # the notifications' own sends (notify.py, #1130)
SYSTEM = (COMMAND_LINE, SETUP_BUILD, SCHEDULED_SYNC, SHARE_LINK, SCHEDULED_BACKUP, TIME_LIMIT, ACCESS_ENDED, WEEKLY_DIRECTORY, START_UP,
          NOTIFICATIONS)


def log(db, action, who=None, profile_id=None, detail=None, ip=None, session_id=None, actor=None, item=None):
    """Record an event. who is the signed-in account (a session row) or None for the system; actor names it
    otherwise (COMMAND_LINE, SETUP_BUILD)."""
    db.execute("INSERT INTO activity(at, profile_id, actor_id, actor, action, detail, ip, session_id, item) VALUES(?,?,?,?,?,?,?,?,?)",
               (dbm.now(), profile_id, who["id"] if who is not None else None,
                who["username"] if who is not None else (actor or SETUP_BUILD), action,
                crypto.enc(json.dumps(detail, separators=(",", ":"))) if detail else None, ip, session_id, item))
    _prune_now_and_then(db)


def once(db, s, profile_id, action, ip, detail=None, key=None):
    """Log action for this session and profile unless it's already logged (key tells documents apart). detail may be
    a function, called only when the entry is made (a document's title is looked up then, #342). The session's own
    person too: a session id from before schema version 39 could be another person's, given again (#588)."""
    if db.execute("""SELECT 1 FROM activity WHERE session_id=? AND actor_id IS ? AND profile_id=? AND action=?
                     AND IFNULL(item, '')=?""", (s["sid"], s["id"], profile_id, action, key or "")).fetchone():
        return
    log(db, action, s, profile_id, detail() if callable(detail) else detail, ip, s["sid"], item=key)


def _prune_now_and_then(db):
    now = dbm.now()
    last = int(dbm.meta_get(db, "activity_pruned") or 0)
    if now - last < 86400:
        return
    db.execute("DELETE FROM activity WHERE at < ?", (now - config.setting("ACTIVITY_DAYS") * 86400,))
    dbm.meta_set(db, "activity_pruned", str(now))


def _rows(db, where, args, before, limit):
    q = f"SELECT id, at, profile_id, actor_id, actor, action, detail, ip FROM activity WHERE {where}"
    if before:
        q, args = q + " AND id < ?", (*args, before)
    out = []
    for r in db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, min(max(limit, 1), 500))):
        d = dict(r)
        d["detail"] = json.loads(crypto.dec(d["detail"])) if d["detail"] else {}
        d["detail"].pop("key", None)   # entries from before the item column
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
