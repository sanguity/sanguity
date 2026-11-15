"""Bursts of failed sign-ins, for admins to see (ASVS 8.1.4).

Every failed sign-in is also written to the log (main._audit); here it is kept for KEEP_DAYS in the database too:
when, from which address, and which account if the name was a real one (a typed name that matches no account is
not kept: people sometimes type a password there). Admins get a notice on the Account page when, since the last
dismissal and within the past WINDOW seconds, one address or one account reaches PER_SOURCE failures, or all of
them together reach TOTAL. Nothing is sent anywhere.
"""
import logging

from . import db as dbm

KEEP_DAYS, WINDOW, PER_SOURCE, TOTAL, TOP = 30, 86400, 10, 30, 5
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
    return {"since": since, "last": last, "total": total, "addresses": addresses, "accounts": accounts}


def dismiss(db):
    dbm.meta_set(db, SEEN, str(dbm.now()))
