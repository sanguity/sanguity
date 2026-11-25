"""Notices for admins: bursts of failed sign-ins (ASVS 8.1.4), accounts whose authenticator codes stopped working
after too many wrong ones (#928), and connected sources that stopped syncing (#102).

Every failed sign-in is also written to the log (main._audit); here it is kept for KEEP_DAYS in the database too:
when, from which address, and which account if the name was a real one (a typed name that matches no account is
not kept: people sometimes type a password there). So is a signed-in session's wrong password or code (#931: the
fresh check, a password change, turning two-factor sign-in on or off, a passkey added or removed), step
"check: ..." (CHECK), which the notice counts apart as "checks". Admins get a notice on the Account page when, since the last
dismissal and within the past WINDOW seconds, one address or one account reaches PER_SOURCE failures, or all of
them together reach TOTAL. Nothing is sent anywhere.
"""
import logging

from . import db as dbm

KEEP_DAYS, WINDOW, PER_SOURCE, TOTAL, TOP = 30, 86400, 10, 30, 5
CHECK = "check: "   # a signed-in session's wrong password or code, not a sign-in's (#931)
SEEN = "signin_notice_seen"
log = logging.getLogger("sanguity")


def record(ip, uid, step):
    """Keep one failed sign-in. Never raises: a sign-in's answer doesn't depend on it."""
    try:
        db = dbm.connect()
        try:
            now = dbm.now()
            db.execute("INSERT INTO signin_failures(at, ip, user_id, step) VALUES(?, ?, ?, ?)",
                       (now, (ip or "?")[:64], uid, (step or "")[:20]))
            db.execute("DELETE FROM signin_failures WHERE at<?", (now - KEEP_DAYS * 86400,))
        finally:
            db.close()
    except Exception:   # noqa: BLE001  (a full disk or a locked database must not turn into a failed sign-in)
        log.exception("couldn't record a failed sign-in")


def notice(db):
    """What admins should see, or None when nothing stands out."""
    since = max(dbm.now() - WINDOW, int(dbm.meta_get(db, SEEN) or 0))
    total, last = db.execute("SELECT COUNT(*), MAX(at) FROM signin_failures WHERE at>?", (since,)).fetchone()
    if not total:
        return None
    addresses = [dict(r) for r in db.execute(
        "SELECT ip, COUNT(*) AS failures, MAX(at) AS last FROM signin_failures WHERE at>? GROUP BY ip ORDER BY failures DESC, last DESC LIMIT ?",
        (since, TOP))]
    accounts = [dict(r) for r in db.execute(
        """SELECT u.username, COUNT(*) AS failures, MAX(f.at) AS last FROM signin_failures f JOIN users u ON u.id = f.user_id
           WHERE f.at>? GROUP BY f.user_id ORDER BY failures DESC, last DESC LIMIT ?""", (since, TOP))]
    peak = max([a["failures"] for a in addresses] + [a["failures"] for a in accounts] + [0])
    if total < TOTAL and peak < PER_SOURCE:
        return None
    checks = db.execute("SELECT COUNT(*) FROM signin_failures WHERE at>? AND step LIKE ?", (since, CHECK + "%")).fetchone()[0]
    return {"since": since, "last": last, "total": total, "checks": checks, "addresses": addresses, "accounts": accounts}


def codes_stopped(db):
    """Accounts whose sign-in with an authenticator code has stopped (#928: twofactor.STOP_AFTER wrong codes since the
    last right one), with when: [{"username", "since"}]. It lasts while it's true: a recovery code or an admin's reset
    of two-factor sign-in ends it."""
    from . import twofactor
    return [dict(r) for r in db.execute(
        """SELECT u.username, (SELECT MAX(a.at) FROM activity a WHERE a.profile_id IS NULL AND a.action='2fa.stopped'
                               AND a.actor_id = u.id) AS since
           FROM users u WHERE u.totp_secret IS NOT NULL AND u.totp_fails >= ? ORDER BY u.username COLLATE NOCASE""",
        (twofactor.STOP_AFTER,))]


def dismiss(db):
    dbm.meta_set(db, SEEN, str(dbm.now()))


def sync_notice(db):
    """Connections on this install that need looking at, per source: [{source, access, failing, stale}] (counts
    only: no profile names or records). It lasts while it's true; there is nothing to dismiss. Nothing is sent
    anywhere: the Account page shows it."""
    from . import connections
    return connections.install_warnings(db)
