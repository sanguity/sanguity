"""Command-line administration, for recovery and scripting. In Docker:  docker compose exec app python -m server.manage ...

  list                                   accounts, profiles and who has which role
  create-user USERNAME [--admin]         new account with a one-time password (changed at first sign-in)
  reset-password USERNAME [--enable]     new one-time password; signs the account out everywhere
                                         (--enable also turns a disabled account back on)
  share PROFILE_ID USERNAME ROLE         give an account owner, editor or viewer access (or change it)
  create-profile NAME USERNAME           a new, empty profile owned by USERNAME (prints its number)
  import PROFILE_ID DIR|EXPORT.zip       load DIR/data.json (and settings.json), or an export from the Account page,
                                         into a profile (replacing its records)
  sync                                   re-import the setup build into the profiles linked to it
  backup [DEST] [--keep N]               write a backup archive (default folder: /backups in Docker), keeping the
                                         newest N there; safe while the app runs
  disable-2fa USERNAME                   turn off someone's two-factor sign-in (lost phone and recovery codes);
                                         signs them out everywhere
  verify ARCHIVE                         check a backup is complete and undamaged
  restore ARCHIVE [--yes]                replace the database, profiles and setup build with a backup's; the app must
                                         be stopped: docker compose run --rm --no-deps app python -m server.manage restore ...
"""
import os, sys

from . import accounts, activity, backup, db as dbm, export, main, profiles as prof, security as sec


def _user(db, name):
    u = db.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
    return u or sys.exit(f"No account named {name!r}.")


def _backup_command(cmd, args):
    """backup, verify and restore: these work on the files, so no database connection is held open meanwhile."""
    try:
        if cmd == "backup":
            keep = int(args[args.index("--keep") + 1]) if "--keep" in args else None
            dest = next((a for a in args if not a.startswith("--") and a != str(keep)), None)
            path = backup.create(dest, keep)
            print(f"Backup written: {path}")
        elif cmd == "verify" and args:
            m = backup.verify(args[0])
            print(f"OK: backup from {m['created']}, {len(m['files'])} files, database schema {m['schema']}.")
        elif cmd == "restore" and args:
            m = backup.verify(args[0])
            print(f"Backup from {m['created']}: {len(m['files'])} files. Restoring replaces this install's accounts, "
                  "profiles and records with the backup's (the current ones are moved aside, not deleted).")
            if "--yes" not in args and (not sys.stdin.isatty() or input("Type yes to restore: ").strip().lower() != "yes"):
                sys.exit("Nothing changed. (Add --yes to restore without being asked.)")
            m, aside = backup.restore(args[0])
            print(f"Restored. The data it replaced is in {aside}. Start the app again.")
        else:
            sys.exit(__doc__)
    except backup.BackupError as e:
        sys.exit(str(e))


def run(argv):
    if argv and argv[0] in ("backup", "verify", "restore"):
        return _backup_command(argv[0], argv[1:])
    dbm.init()
    db = dbm.connect()
    try:
        _command(db, argv)
    finally:
        db.close()


def _command(db, argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
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
        print(f"Created {args[0].strip()}. One-time password: {pw}  (they choose their own at first sign-in)")
    elif cmd == "reset-password" and args:
        u, pw = _user(db, args[0]), accounts.temp_password()
        enable = "--enable" in args
        db.execute("UPDATE users SET pw_hash=?, must_change_pw=1, disabled=CASE WHEN ? THEN 0 ELSE disabled END WHERE id=?",
                   (sec.hash_password(pw), enable, u["id"]))
        accounts.sign_out_everywhere(db, u["id"])
        activity.log(db, "account.reset", detail={"username": u["username"]}, actor=activity.COMMAND_LINE)
        if enable and u["disabled"]:
            activity.log(db, "account.enable", detail={"username": u["username"]}, actor=activity.COMMAND_LINE)
        print(f"One-time password for {u['username']}: {pw}  (signed out everywhere; they choose a new one at sign-in)")
        if u["disabled"] and not enable:
            print("Note: this account is disabled, so it still can't sign in. Add --enable to turn it back on.")
        elif u["disabled"]:
            print("The account is enabled again.")
    elif cmd == "share" and len(args) == 3 and args[2] in main.RANK:
        pid, u = int(args[0]), _user(db, args[1])
        if not db.execute("SELECT 1 FROM profiles WHERE id=?", (pid,)).fetchone():
            sys.exit(f"No profile {pid}.")
        old = main.role_of(db, u["id"], pid)
        db.execute("""INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)
                      ON CONFLICT(user_id, profile_id) DO UPDATE SET role=excluded.role""", (u["id"], pid, args[2]))
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
    elif cmd == "disable-2fa" and args:
        u = _user(db, args[0])
        accounts.turn_off_2fa(db, u["id"])
        activity.log(db, "2fa.reset", detail={"username": u["username"]}, actor=activity.COMMAND_LINE)
        print(f"Two-factor sign-in is off for {u['username']}; they're signed out everywhere."
              + (" Their password was only allowed with it, so they'll choose a new one at sign-in." if u["pw_short"] else ""))
    elif cmd == "create-profile" and len(args) == 2:
        u = _user(db, args[1])
        pid = prof.create_profile(db, args[0].strip()[:80] or "Profile", owner_id=u["id"])
        activity.log(db, "create", profile_id=pid, actor=activity.COMMAND_LINE)
        print(f"Created profile {pid}, \"{args[0].strip()[:80]}\", owned by {u['username']}.")
    elif cmd == "sync":
        failed = prof.sync_build(db)
        if failed:
            sys.exit("\n".join(f"Profile {p}: couldn't load the setup build ({why})" for p, why in failed.items()))
        print("Linked profiles are up to date with the setup build.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    run(sys.argv[1:])
