"""Creating accounts, shared by the API (setup wizard, admin page) and the command line (server.manage)."""
import os, secrets, sqlite3

from . import config, db as dbm, security as sec


class AccountError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def temp_password():
    return "-".join(secrets.token_urlsafe(6) for _ in range(3))   # 3 x 8 characters; must be changed at first sign-in


def create(db, username, is_admin=False, password=None):
    """New account. With no password, it gets a one-time one (returned) that its owner replaces at first sign-in.
    Returns (user id, one-time password or None)."""
    name = username.strip()
    if (p := sec.username_problem(name) or (password is not None and sec.password_problem(password, name))):
        raise AccountError(p)
    if db.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
        raise AccountError("That username is taken.", 409)
    temp = None if password is not None else temp_password()
    try:
        uid = db.execute("INSERT INTO users(username, pw_hash, is_admin, must_change_pw, created) VALUES(?, ?, ?, ?, ?)",
                         (name, sec.hash_password(password if password is not None else temp), int(is_admin),
                          int(temp is not None), dbm.now())).lastrowid
    except sqlite3.IntegrityError:   # created by someone else since the check above (the UNIQUE index decides)
        raise AccountError("That username is taken.", 409)
    return uid, temp


def sign_out_everywhere(db, uid):
    """End every session and forget every known browser of an account (disabled, or given a new password)."""
    db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    db.execute("DELETE FROM devices WHERE user_id=?", (uid,))


def forget_setup_code(db):
    """First-time setup is over: the code no longer works, and its file is gone."""
    dbm.meta_set(db, "setup_code", None)
    try:
        os.remove(config.SETUP_CODE_FILE)
    except FileNotFoundError:
        pass


def new_recovery_codes(db, uid):
    """Replace an account's recovery codes; returns the new ones (shown once, stored only as hashes)."""
    from . import twofactor
    codes = twofactor.new_recovery_codes()
    db.execute("DELETE FROM recovery_codes WHERE user_id=?", (uid,))
    db.executemany("INSERT INTO recovery_codes(user_id, code_hash) VALUES(?, ?)", [(uid, twofactor.recovery_hash(c)) for c in codes])
    return codes


def turn_off_2fa(db, uid):
    """Two-factor sign-in off: secret and recovery codes gone, signed out everywhere, and a password that was only
    allowed because of the second factor has to be replaced at the next sign-in."""
    db.execute("""UPDATE users SET totp_secret=NULL, totp_pending=NULL, totp_last_step=0,
                  must_change_pw=CASE WHEN pw_short THEN 1 ELSE must_change_pw END WHERE id=?""", (uid,))
    db.execute("DELETE FROM recovery_codes WHERE user_id=?", (uid,))
    sign_out_everywhere(db, uid)


def two_factor_required(db):
    return dbm.meta_get(db, "require_2fa") == "1"


def claim_unowned(db, uid):
    """Make uid the owner of every profile nobody has access to yet (the records already on a new install), and
    finish first-time setup. Returns those profile ids."""
    pids = [r[0] for r in db.execute("SELECT id FROM profiles WHERE id NOT IN (SELECT profile_id FROM memberships) ORDER BY id")]
    for pid in pids:
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (uid, pid))
    forget_setup_code(db)
    return pids
