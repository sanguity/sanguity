"""Command-line administration, for recovery and scripting. In Docker:  docker compose exec app python -m server.manage ...

  list                                   accounts, profiles and who has which role
  create-user USERNAME [--admin]         new account with a one-time password (changed at first sign-in)
  reset-password USERNAME [--enable]     new one-time password; signs the account out everywhere
                                         (--enable also turns a disabled account back on)
  share PROFILE_ID USERNAME ROLE         give an account owner, editor or viewer access (or change it)
  create-profile NAME USERNAME           a new, empty profile owned by USERNAME (prints its number)
  link-build PROFILE_ID                  load the setup build (your exports via sources.json) into a profile, and keep
                                         it updated on every rebuild (for after the build's profile was deleted)
  import PROFILE_ID DIR|EXPORT.zip       load DIR/data.json (and settings.json), or an export from the Account page,
                                         into a profile (replacing its records)
  import-apple-health PROFILE_ID PATH    load an iPhone's Health export (the zip from Export All Health Data, or its
                                         export.xml) as the profile's watch and scale summary; a later export
                                         replaces the days and workouts it overlaps
  sync                                   re-import the setup build into the profiles linked to it
  backup [DEST] [--keep N]               write a backup archive (default folder: /backups in Docker), keeping the
                                         newest N there; safe while the app runs
  remove-user USERNAME [--hand-to NAME] [--force]
                                         delete an account for good; profiles it alone owned go to NAME (or, with
                                         --force, are left without an owner)
  disable-2fa USERNAME                   turn off someone's two-factor sign-in, authenticator app and passkeys (lost
                                         phone and recovery codes); signs them out everywhere
  reencrypt                              with the app stopped, after adding a key (setup new-key): re-encrypt everything
                                         with it, so the old key can be removed (starting the app does this too)
  verify ARCHIVE                         check a backup is complete and undamaged
  check ARCHIVE                          restore a backup into a temporary folder beside it, compare it with what it
                                         says it holds, then delete the copy (the Admin page's Check a backup); safe
                                         while the app runs: the live data isn't touched
  restore ARCHIVE [--yes]                replace the records (the database, profiles and setup build) with a backup's,
                                         keeping today's accounts and access, everyone signed out (#917); the app must
                                         be stopped: docker compose run --rm --no-deps app python -m server.manage restore ...
                                         The phone and watch data in it goes to the receiver, which puts it back when
                                         it next starts (#227)
"""
import os, shutil, sys, tempfile, zipfile

from . import accounts, activity, atrest, backup, config, crypto, db as dbm, export, main, profiles as prof, watch_store


APPLE_HEALTH_MAX_BYTES = 16 * 2**30   # export.xml, unzipped: far above a decade of a watch's records


def _apple_health_xml(path, tmp):
    """The export.xml to read: PATH itself, or the one entry of that name in the zip, copied into tmp. Nothing
    else in the zip is unpacked, and a zip with an unsafe entry name or an oversized export.xml is refused."""
    if not zipfile.is_zipfile(path):
        return path
    with zipfile.ZipFile(path) as zf:
        found = None
        for i in zf.infolist():
            name, parts = i.filename, i.filename.split("/")
            if ("\\" in name or name.startswith("/") or ":" in parts[0] or ".." in parts
                    or "" in parts[:-1] or (not i.is_dir() and parts[-1] == "")):
                raise ValueError(f"unexpected entry in the zip: {name}")
            if not i.is_dir() and parts[-1] == "export.xml" and len(parts) <= 2:
                if found:
                    raise ValueError("this zip holds more than one export.xml")
                found = i
        if not found:
            raise ValueError("this zip has no export.xml: it isn't an Apple Health export")
        if found.file_size > APPLE_HEALTH_MAX_BYTES:
            raise ValueError(f"export.xml is more than {APPLE_HEALTH_MAX_BYTES // 2**30} GB unpacked")
        out, n = os.path.join(tmp, "export.xml"), 0
        with zf.open(found) as src, open(out, "wb") as dst:
            while chunk := src.read(1 << 20):
                n += len(chunk)
                if n > APPLE_HEALTH_MAX_BYTES:   # the size in the zip's directory can be wrong
                    raise ValueError(f"export.xml is more than {APPLE_HEALTH_MAX_BYTES // 2**30} GB unpacked")
                dst.write(chunk)
        return out


def import_apple_health(db, pid, path):
    """Parse an Apple Health export and save it as the profile's watch summary. Returns (stored, warnings)."""
    from .importers import apple_health
    tmp = tempfile.mkdtemp(prefix="sanguity-apple-health-")   # removed below: the plaintext copy lives only this long
    try:
        out = apple_health.parse({"apple_health_export/export.xml": _apple_health_xml(path, tmp)})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return watch_store.save_import(db, pid, out["data"]["watch"]), out["warnings"]


def _user(db, name):
    u = db.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
    return u or sys.exit(f"No account named {name!r}.")


ACCESS_WORDS = ("accounts, passwords, passkeys, two-factor sign-in, who may see each profile, share links, calendar feeds, "
                "phone upload secrets and connected sources' sign-ins")


def _say_access(a, created, aside):
    """What a restore did with who may get in (#917, backup._ready), in words."""
    ended = a.get("sessions", 0)
    out = f"Everyone is signed out ({ended} sign-in{'s' if ended != 1 else ''} ended)."
    if a.get("kept"):
        print(f"Today's {ACCESS_WORDS} were kept as they were before the restore; only the records are the backup's. " + out)
    else:
        why = {"none": "There was no database here to keep them from",
               "unreadable": "The database it replaced can't be read (it's damaged; it's kept with what was replaced), so "
                             "they weren't kept from it"}.get(a.get("why"),
               "The database it replaced isn't this install's (another's, or one this key can't read), so they weren't "
               "kept from it")
        print(f"{why}: the {ACCESS_WORDS} are the backup's, as they were on {created}. A share link revoked, a member "
              "removed, an account removed or disabled, or a password or passkey changed since then is as it was then: "
              f"check Admin > Accounts and each profile's Sharing before you share the address again. {out}")
    for pid, name in a.get("back") or ():
        print(f"Profile {pid} ({name}) was deleted after the backup was made and is back, with nobody given access to it: "
              "an admin gives it an owner on Admin > Accounts (Profiles without an owner), or with "
              f"python -m server.manage share {pid} USERNAME owner.")
    for pid, name in a.get("gone") or ():
        print(f"Profile {pid} ({name}) was made after the backup and isn't in it: its records are in {aside}.")


def _backup_command(cmd, args):
    """backup, verify, check and restore: these work on the files, so no database connection is held open meanwhile."""
    try:
        if cmd == "backup":
            keep = int(args[args.index("--keep") + 1]) if "--keep" in args else None
            dest = next((a for a in args if not a.startswith("--") and a != str(keep)), None)
            path = backup.create(dest, keep)
            print(f"Backup written: {path}")
            if backup.verify(path).get("counts", 0) is None:   # (#959)
                print(f"It was written without its row counts: the encryption key at {config.KEY_FILE} couldn't be read, "
                      "and they're sealed with it. Its files are all in it as they are, but Check a backup will say the "
                      "counts can't be compared. Put the key file in place (or set SANGUITY_KEY_FILE) for the next backup.")
        elif cmd == "verify" and args:
            m = backup.verify(args[0])
            print(f"OK: backup from {m['created']}, {len(m['files'])} files, database schema {m['schema']}.")
        elif cmd == "check" and args:
            got = backup.check(args[0])
            if not got["ok"]:
                sys.exit("Not good: " + " ".join(got["problems"]))
            w = got.get("watch")   # the receiver's data (#227)
            print(f"Good: the backup from {got['created']} restored into a temporary folder, {got['files']} files and "
                  f"{sum(c['backup'] for c in got['counts'].values())} database rows as it had when it was made"
                  + (f", and the phone and watch data of {w['profiles']} profile{'s' if w['profiles'] != 1 else ''} "
                     f"({w['counts']['readings']['backup']} readings, {w['counts']['uploads']['backup']} archived uploads)"
                     if w else ", without phone and watch data") + ". The copy was deleted.")
        elif cmd == "restore" and args:
            m = backup.verify(args[0])
            print(f"Backup from {m['created']}: {len(m['files'])} files. Restoring replaces this install's profiles and "
                  f"records with the backup's (the current ones are moved aside, and deleted {backup.KEEP_DAYS} days "
                  f"later), keeping today's {ACCESS_WORDS}. Everyone is signed out.")
            if "--yes" not in args and (not sys.stdin.isatty() or input("Type yes to restore: ").strip().lower() != "yes"):
                sys.exit("Nothing changed. (Add --yes to restore without being asked.)")
            m, aside = backup.restore(args[0])
            print(f"Restored. The data it replaced is in {aside}: the app deletes it {backup.KEEP_DAYS} days from now "
                  "(RESTORE_KEEP_DAYS), or sooner with Delete now on Admin > Backups. It may hold profiles deleted "
                  "since the backup was made. Start the app again.")
            _say_access(m.get("access") or {}, m.get("created"), aside)
            w = m.get("watch_restored")   # the receiver's data (#227)
            if w and w["staged"]:
                print(f"The phone and watch data is waiting for the watch receiver in {w['folder']}. Restart the receiver "
                      "too (docker compose restart receiver; the all-in-one container: restart it): when it starts it "
                      "puts the data back, and moves what it has now to before-restore-<time>/ in its folder.")
            elif w:
                print(f"The phone and watch data is in {w['folder']}: the receiver's keys folder ({os.path.dirname(config.WATCH_KEYS)}) "
                      "can't be written here. With the receiver stopped, move its watch.db, raw/ and profiles/ aside in "
                      "WATCH_DIR, move the ones from that folder in, then start the receiver. The app deletes that folder "
                      f"{backup.KEEP_DAYS} days from now, or sooner with Delete now on Admin > Backups, where it's listed: "
                      "it may hold profiles deleted since, and the backup still has it.")
            else:
                print("This backup has no phone and watch data (made by an older version, or there was none): the "
                      "watch receiver's data was left as it is.")
        else:
            sys.exit(__doc__)
    except backup.BackupError as e:
        sys.exit(str(e))


def run(argv):
    atrest.keep_private(walk=False)   # what a command makes is the app's user's alone, as the app's is (#935)
    if argv and argv[0] in ("backup", "verify", "check", "restore"):
        return _backup_command(argv[0], argv[1:])
    dbm.init()
    db = dbm.connect()
    try:
        _command(db, argv)
    finally:
        db.close()


def _command(db, argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd not in ("", "list"):   # passwords are hashed with the pepper, which the data key unlocks
        atrest.check_key(db)
        atrest.load_pepper(db)
    if cmd == "list":
        for u in db.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE"):
            flags = [f for f, on in (("admin", u["is_admin"]), ("disabled", u["disabled"]), ("must change password", u["must_change_pw"])) if on]
            print(f"user {u['id']:>3}  {u['username']}" + (f"  ({', '.join(flags)})" if flags else ""))
        for p in db.execute("SELECT * FROM profiles ORDER BY id"):
            who = ", ".join(f"{r['username']}={r['role']}" for r in db.execute(
                "SELECT u.username, m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.profile_id=?", (p["id"],)))
            print(f"profile {p['id']:>3}  {p['name']}" + ("  [setup build]" if p["linked_build"] else "") + f"  {who or 'no members'}")
    elif cmd == "create-user" and args:
        try:
            uid, pw = accounts.create(db, args[0], is_admin="--admin" in args)
        except accounts.AccountError as e:
            sys.exit(str(e))
        activity.log(db, "account.create", detail={"username": args[0].strip(), **({"admin": True} if "--admin" in args else {})},
                     actor=activity.COMMAND_LINE)
        if "--admin" in args:   # an admin created here also finishes first-time setup
            accounts.claim_unowned(db, uid)
        print(f"Created {args[0].strip()}. One-time password: {pw}  (they choose their own at first sign-in, within {config.TEMP_PASSWORD_DAYS} days)")
    elif cmd == "reset-password" and args:
        u, enable = _user(db, args[0]), "--enable" in args
        pw = accounts.set_temp_password(db, u["id"])
        if enable:
            accounts.end_lost_access(db)   # what ended while it was disabled stays ended (#590)
            db.execute("UPDATE users SET disabled=0 WHERE id=?", (u["id"],))
        activity.log(db, "account.reset", detail={"username": u["username"]}, actor=activity.COMMAND_LINE)
        if enable and u["disabled"]:
            activity.log(db, "account.enable", detail={"username": u["username"]}, actor=activity.COMMAND_LINE)
        print(f"One-time password for {u['username']}: {pw}  (signed out everywhere; they choose a new one at sign-in, within {config.TEMP_PASSWORD_DAYS} days)")
        if u["disabled"] and not enable:
            print("Note: this account is disabled, so it still can't sign in. Add --enable to turn it back on.")
        elif u["disabled"]:
            print("The account is enabled again.")
    elif cmd == "share" and len(args) == 3 and args[2] in main.RANK:
        pid, u = int(args[0]), _user(db, args[1])
        if not db.execute("SELECT 1 FROM profiles WHERE id=?", (pid,)).fetchone():
            sys.exit(f"No profile {pid}.")
        old = main.role_of(db, u["id"], pid)
        accounts.end_lost_access(db)   # as the app's Sharing does (#590): what ended with lost access stays ended
        db.execute("""INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)
                      ON CONFLICT(user_id, profile_id) DO UPDATE SET role=excluded.role""", (u["id"], pid, args[2]))
        accounts.end_lost_access(db)
        if old != args[2]:
            activity.log(db, "share", profile_id=pid, detail={"username": u["username"], "role": args[2], **({"from": old} if old else {})},
                         actor=activity.COMMAND_LINE)
        print(f"{u['username']} is now {args[2]} of profile {pid}.")
    elif cmd == "import" and len(args) == 2:
        pid = int(args[0])
        if not db.execute("SELECT 1 FROM profiles WHERE id=?", (pid,)).fetchone():
            sys.exit(f"No profile {pid}.")
        linked = db.execute("SELECT linked_build FROM profiles WHERE id=?", (pid,)).fetchone()[0]
        db.execute("UPDATE profiles SET linked_build=0 WHERE id=?", (pid,))   # it now holds its own data...
        try:
            n = (export.import_archive if os.path.isfile(args[1]) else prof.import_dataset)(db, pid, args[1])
        except BaseException:   # ...unless the import failed: then it stays as it was
            db.execute("UPDATE profiles SET linked_build=? WHERE id=?", (linked, pid))
            raise
        activity.log(db, "import", profile_id=pid, detail={"documents": n}, actor=activity.COMMAND_LINE)
        print(f"Imported {args[1]} into profile {pid} ({n} documents).")
    elif cmd == "import-apple-health" and len(args) == 2:
        pid = int(args[0])
        if not db.execute("SELECT 1 FROM profiles WHERE id=?", (pid,)).fetchone():
            sys.exit(f"No profile {pid}.")
        if not os.path.isfile(args[1]):
            sys.exit(f"No file {args[1]}.")
        try:
            stored, warnings = import_apple_health(db, pid, args[1])
        except (ValueError, zipfile.BadZipFile) as e:
            sys.exit(f"Couldn't import {args[1]}: {e}")
        activity.log(db, "import", profile_id=pid, actor=activity.COMMAND_LINE,
                     detail={"documents": 0, "source": "apple-health", "days": stored["days"], "workouts": stored["workouts"]})
        for w in warnings:
            print("Note:", w)
        print(f"Imported Apple Health into profile {pid}: {stored['days']} days, {stored['workouts']} workouts.")
    elif cmd == "remove-user" and args:
        u = _user(db, args[0])
        hand = _user(db, args[args.index("--hand-to") + 1]) if "--hand-to" in args else None
        sole = [name for _, name in accounts.sole_owned(db, u["id"])]
        if sole and not hand and "--force" not in args:
            sys.exit(f"{u['username']} is the only owner of {', '.join(sole)}. Add --hand-to USERNAME, or --force to leave "
                     "them without an owner.")
        if u["is_admin"] and db.execute("SELECT COUNT(*) FROM users WHERE is_admin=1 AND disabled=0 AND id<>?", (u["id"],)).fetchone()[0] == 0:
            sys.exit("That's the last active admin.")
        gone = accounts.remove(db, u["id"], hand["id"] if hand else None)
        activity.log(db, "account.remove", detail=gone, actor=activity.COMMAND_LINE)
        print(f"Removed {gone['username']}." + (f" {hand['username']} now owns {', '.join(gone['handed_over'])}." if gone["handed_over"] else ""))
    elif cmd == "disable-2fa" and args:
        u = _user(db, args[0])
        accounts.turn_off_2fa(db, u["id"], passkeys_too=True)
        activity.log(db, "2fa.reset", detail={"username": u["username"]}, actor=activity.COMMAND_LINE)
        print(f"Two-factor sign-in (authenticator app and passkeys) is off for {u['username']}; they're signed out everywhere."
              + (" Their password was only allowed with it, so they'll choose a new one at sign-in." if u["pw_short"] else ""))
    elif cmd == "create-profile" and len(args) == 2:
        u = _user(db, args[1])
        pid = prof.create_profile(db, args[0].strip()[:80] or "Profile", owner_id=u["id"])
        activity.log(db, "create", profile_id=pid, actor=activity.COMMAND_LINE)
        print(f"Created profile {pid}, \"{args[0].strip()[:80]}\", owned by {u['username']}.")
    elif cmd == "link-build" and args:
        pid = int(args[0])
        if not db.execute("SELECT 1 FROM profiles WHERE id=?", (pid,)).fetchone():
            sys.exit(f"No profile {pid}.")
        failed = prof.link_build(db, pid)
        if failed:
            sys.exit(f"Linked, but the build couldn't be loaded: {failed[pid]}")
        print(f"Profile {pid} now shows the setup build, and follows every rebuild.")
    elif cmd == "reencrypt":
        if backup._app_is_running():   # it would go on writing with the key it loaded, and lose the new one
            sys.exit("The app is running: restart it instead (docker compose restart app), which re-encrypts as it starts.")
        atrest.check_key(db)
        done = atrest.seal_everything(db)
        print(f"Re-encrypted {done['files']} files and {done['fields']} database fields with the newest key "
              f"({crypto.current_key_id().hex()}). Older keys can now be removed from the key file.")
    elif cmd == "sync":
        failed = prof.sync_build(db)
        if failed:
            sys.exit("\n".join(f"Profile {p}: couldn't load the setup build ({why})" for p, why in failed.items()))
        print("Linked profiles are up to date with the setup build.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    run(sys.argv[1:])
