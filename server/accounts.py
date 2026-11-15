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
        uid = db.execute("""INSERT INTO users(username, pw_hash, is_admin, must_change_pw, temp_pw_expires, created)
                            VALUES(?, ?, ?, ?, ?, ?)""",
                         (name, sec.hash_password(password if password is not None else temp), int(is_admin),
                          int(temp is not None), temp_expiry() if temp is not None else None, dbm.now())).lastrowid
    except sqlite3.IntegrityError:   # created by someone else since the check above (the UNIQUE index decides)
        raise AccountError("That username is taken.", 409)
    return uid, temp


def temp_expiry():
    return dbm.now() + config.TEMP_PASSWORD_DAYS * 86400


def set_temp_password(db, uid):
    """Give an account a new one-time password (an admin's reset): it must be replaced at the next sign-in, stops
    working after TEMP_PASSWORD_DAYS, and every session and known browser of the account ends. Returns it."""
    pw = temp_password()
    db.execute("UPDATE users SET pw_hash=?, must_change_pw=1, temp_pw_expires=? WHERE id=?", (sec.hash_password(pw), temp_expiry(), uid))
    sign_out_everywhere(db, uid)
    return pw


def temp_password_expired(u):
    return bool(u["temp_pw_expires"]) and u["temp_pw_expires"] <= dbm.now()


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


def has_second_factor(db, u):
    """An authenticator app, or a passkey (which checks the person itself)."""
    return bool(u["totp_secret"]) or db.execute("SELECT 1 FROM passkeys WHERE user_id=? LIMIT 1", (u["id"],)).fetchone() is not None


def solo_install(db):
    """One account and nobody else on the server."""
    return db.execute("SELECT COUNT(*) FROM users").fetchone()[0] <= 1


def admin_needs_second_factor(db, u):
    """Admin actions need the admin's own second factor (ASVS 4.3.1), except on a one-person install: there the
    admin is the only account, so the rule starts once a second account exists."""
    return bool(u["is_admin"]) and not has_second_factor(db, u) and not solo_install(db)


def short_password_check(db, uid):
    """After a second factor went: a password only allowed because of it has to be replaced at the next sign-in."""
    u = db.execute("SELECT id, totp_secret FROM users WHERE id=?", (uid,)).fetchone()
    if not has_second_factor(db, u):
        db.execute("UPDATE users SET must_change_pw=1 WHERE id=? AND pw_short=1", (uid,))


def turn_off_2fa(db, uid, passkeys_too=False):
    """The authenticator app off (and with passkeys_too, every passkey: an admin's reset for a lost phone): secret
    and recovery codes gone, signed out everywhere, and a password that was only allowed because of a second factor
    has to be replaced at the next sign-in."""
    db.execute("UPDATE users SET totp_secret=NULL, totp_pending=NULL, totp_last_step=0 WHERE id=?", (uid,))
    db.execute("DELETE FROM recovery_codes WHERE user_id=?", (uid,))
    if passkeys_too:
        db.execute("DELETE FROM passkeys WHERE user_id=?", (uid,))
    short_password_check(db, uid)
    sign_out_everywhere(db, uid)


def sole_owned(db, uid):
    """Profiles this account is the only active owner of: [(id, name)]. Without it nobody could share or rename them."""
    return [(r["id"], r["name"]) for r in db.execute(
        """SELECT p.id, p.name FROM memberships m JOIN profiles p ON p.id = m.profile_id
           WHERE m.user_id=? AND m.role='owner' ORDER BY p.name COLLATE NOCASE""", (uid,)).fetchall()
        if not dbm.active_owners(db, r["id"], excluding=uid)]


def remove(db, uid, hand_to=None):
    """Delete an account for good: its sessions, known browsers, recovery codes, passkeys and memberships go with it (the
    schema cascades); its activity entries stay, under its old name. hand_to (another account's id) first becomes
    owner of the profiles this one was the only active owner of. Returns {"username", "handed_over": [names]}."""
    u = db.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    handed = []
    with dbm.transaction(db):
        if hand_to is not None:
            for pid, name in sole_owned(db, uid):
                db.execute("""INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')
                              ON CONFLICT(user_id, profile_id) DO UPDATE SET role='owner'""", (hand_to, pid))
                handed.append(name)
        db.execute("DELETE FROM users WHERE id=?", (uid,))
    return {"username": u["username"], "handed_over": handed}


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
