"""SQLite database: schema and small helpers. One connection per request; WAL so readers never block."""
import os, sqlite3, time
from contextlib import contextmanager

from . import config

SCHEMA = [
    # version 1: accounts, profiles, roles, sessions, documents
    """
    CREATE TABLE users(
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        pw_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        must_change_pw INTEGER NOT NULL DEFAULT 0,
        created INTEGER NOT NULL);
    CREATE TABLE profiles(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        created INTEGER NOT NULL,
        linked_build INTEGER NOT NULL DEFAULT 0,   -- 1: kept in sync with the data that setup builds
        build_mtime,                               -- which build was loaded last (profiles.build_version)
        watch_file TEXT);                          -- the receiver's daily.json for this person, if any
    CREATE TABLE memberships(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK(role IN ('owner', 'editor', 'viewer')),
        PRIMARY KEY(user_id, profile_id));
    CREATE INDEX memberships_profile ON memberships(profile_id);
    CREATE TABLE sessions(
        id INTEGER PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,           -- SHA-256 of the cookie value; the value itself is never stored
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        expires INTEGER NOT NULL,
        ip TEXT,
        agent TEXT);
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE documents(
        id TEXT PRIMARY KEY,                       -- random, so ids can't be guessed or enumerated
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        ext TEXT NOT NULL);
    CREATE INDEX documents_profile ON documents(profile_id);
    CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
    """,
    # version 2: browsers that have signed in to an account before, so other people's failed guesses can't
    # lock that account's owner out (see security.Throttle)
    """
    CREATE TABLE devices(
        token_hash TEXT NOT NULL,                  -- SHA-256 of the sg_device cookie; one browser, so a shared
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- tablet has a row per account
        created INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        PRIMARY KEY(token_hash, user_id));
    CREATE INDEX devices_user ON devices(user_id);
    """,
    # version 3: the activity log (see activity.py)
    """
    CREATE TABLE activity(
        id INTEGER PRIMARY KEY,
        at INTEGER NOT NULL,
        profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,   -- NULL: an account event
        actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor TEXT NOT NULL,                       -- the username then (or "command line", "setup build")
        action TEXT NOT NULL,
        detail TEXT,                               -- JSON
        ip TEXT,
        session_id INTEGER);                       -- to log a view once per session
    CREATE INDEX activity_profile ON activity(profile_id, id);
    CREATE INDEX activity_seen ON activity(session_id, profile_id, action);
    """,
    # version 4: two-factor sign-in (see twofactor.py)
    """
    ALTER TABLE users ADD COLUMN totp_secret TEXT;                    -- set: two-factor sign-in is on
    ALTER TABLE users ADD COLUMN totp_pending TEXT;                   -- being set up, not confirmed yet
    ALTER TABLE users ADD COLUMN totp_last_step INTEGER NOT NULL DEFAULT 0;   -- the last code's step: no reuse
    ALTER TABLE users ADD COLUMN pw_short INTEGER NOT NULL DEFAULT 0; -- password shorter than the one-factor minimum
    CREATE TABLE recovery_codes(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code_hash TEXT NOT NULL,                   -- SHA-256 of the code; the code itself is shown once
        used_at INTEGER,
        PRIMARY KEY(user_id, code_hash));
    """,
]


def connect():
    # autocommit (explicit BEGIN where needed). One connection per request, but FastAPI may run a request's
    # dependencies and handler on different worker threads, so the same-thread check is off.
    db = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    return db


def init():
    """Create the data folder and database, and run any schema steps not yet applied."""
    os.makedirs(os.path.join(config.DATA_DIR, "profiles"), exist_ok=True)
    db = connect()
    db.execute("PRAGMA journal_mode=WAL")
    have = db.execute("PRAGMA user_version").fetchone()[0]
    for v, sql in enumerate(SCHEMA[have:], start=have + 1):
        db.executescript("BEGIN;" + sql + f"PRAGMA user_version={v};COMMIT;")
    db.close()


@contextmanager
def transaction(db):
    """BEGIN IMMEDIATE (take the write lock now, so checks inside see what they will change), then COMMIT, or
    ROLLBACK if anything inside raises."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def active_owners(db, pid, excluding=None):
    """Owners of a profile whose accounts aren't disabled: the people who can still share and rename it."""
    return db.execute("""SELECT COUNT(*) FROM memberships m JOIN users u ON u.id = m.user_id
                         WHERE m.profile_id=? AND m.role='owner' AND u.disabled=0 AND m.user_id IS NOT ?""",
                      (pid, excluding)).fetchone()[0]


def now():
    return int(time.time())


def meta_get(db, k):
    r = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def meta_set(db, k, v):
    if v is None:
        db.execute("DELETE FROM meta WHERE k=?", (k,))
    else:
        db.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
