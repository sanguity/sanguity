"""SQLite database: schema and small helpers. One connection per request; WAL so readers never block."""
import os, sqlite3, time
from contextlib import contextmanager

from . import config

# docs/database.md has diagrams of what these steps build; update them when you add one
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
    # version 5: encryption at rest (crypto.py). Activity details are encrypted, so the key that makes a view
    # "once per session" (a document id) moves to its own column.
    """
    ALTER TABLE activity ADD COLUMN item TEXT;
    DROP INDEX activity_seen;
    CREATE INDEX activity_seen ON activity(session_id, profile_id, action, item);
    """,
    # version 6: passkeys (see passkeys.py). Each is bound to the site name it was made at (rp_id).
    """
    ALTER TABLE users ADD COLUMN webauthn_handle TEXT;   -- random id the authenticator stores for the account
    CREATE TABLE passkeys(
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        credential_id TEXT NOT NULL UNIQUE,             -- base64url
        public_key BLOB NOT NULL,                       -- COSE; public, so not encrypted
        sign_count INTEGER NOT NULL DEFAULT 0,
        rp_id TEXT NOT NULL,
        name TEXT NOT NULL,
        transports TEXT,                                -- JSON list, a hint for the browser
        created INTEGER NOT NULL,
        last_used INTEGER);
    CREATE INDEX passkeys_user ON passkeys(user_id);
    """,
    # version 7: one-time passwords expire (accounts.set_temp_password). NULL: the password is the person's own
    # (or a one-time one from before this version, which keeps working until it's replaced).
    """
    ALTER TABLE users ADD COLUMN temp_pw_expires INTEGER;
    """,
    # version 8: failed sign-ins, kept 30 days so admins see bursts (alerts.py). user_id only when the name was a
    # real account.
    """
    CREATE TABLE signin_failures(
        at INTEGER NOT NULL,
        ip TEXT,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        step TEXT);
    CREATE INDEX signin_failures_at ON signin_failures(at);
    """,
    # version 9: when the session's person last proved it was them (signing in, or the fresh check before the most
    # powerful admin actions, main.fresh). NULL, for sessions from before: the next such action asks.
    """
    ALTER TABLE sessions ADD COLUMN verified_at INTEGER;
    """,
    # version 10: records as rows instead of one data.json per profile (records.py). Each row belongs to a profile
    # and to the import that brought it in, so an import can be replaced or undone as a unit. What the rows hold
    # is health information, so it is encrypted (crypto.enc): only ids and the links between rows are plain.
    # Each profile's existing data.json is loaded into these tables at the next start (records.migrate_files).
    """
    CREATE TABLE imports(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        at INTEGER NOT NULL,
        origin TEXT NOT NULL,                      -- build: the setup build; dataset: a data.json folder or an
                                                   -- export zip (server.manage import); upload: files uploaded
                                                   -- in the browser, read by an importer (jobs.py). An upload
                                                   -- adds to the profile's records instead of replacing them
        format TEXT NOT NULL,                      -- the reader that produced it ("sanguity-data": a data.json)
        meta TEXT);                                -- JSON: when it was built, its source, and the profile-wide
                                                   -- lists it brought (upcoming visits, problems, payments...)
    CREATE INDEX imports_profile ON imports(profile_id);
    CREATE TABLE events(                           -- a visit, stay, document, claim, lab draw, message...
        id INTEGER PRIMARY KEY,                    -- the id the page sees
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
        body TEXT NOT NULL,                        -- JSON: the event as the page reads it, without its id
        detail TEXT,                               -- JSON: what the drawer shows for it
        cost TEXT,                                 -- JSON: its row on the Costs tab (claims)
        cost_seq INTEGER);                         -- where that row came in the import's list of costs
    CREATE INDEX events_profile ON events(profile_id);
    CREATE INDEX events_import ON events(import_id);
    CREATE TABLE series(                           -- a lab or metric across time
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
        body TEXT NOT NULL);                       -- JSON: name, unit, panel, LOINC code, text results
    CREATE INDEX series_profile ON series(profile_id);
    CREATE INDEX series_import ON series(import_id);
    CREATE TABLE results(                          -- one value of a series
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
        series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
        body TEXT NOT NULL);                       -- JSON: [time, value, low, high, flag, as reported, source]
    CREATE INDEX results_series ON results(series_id);
    CREATE INDEX results_import ON results(import_id);
    ALTER TABLE documents ADD COLUMN import_id INTEGER REFERENCES imports(id) ON DELETE CASCADE;
    ALTER TABLE profiles ADD COLUMN records_rev INTEGER NOT NULL DEFAULT 0;   -- bumped by every change to its records
    """,
    # version 11: uploads, the raw files of an import as the browser sent them (uploads.py). The files are in
    # profiles/<id>/uploads/<upload id>/ under random names; which name holds which path is in the manifest,
    # encrypted like any health detail (file names can say whose records they are).
    """
    CREATE TABLE uploads(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        created INTEGER NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- NULL: the command line, or a removed account
        state TEXT NOT NULL CHECK(state IN ('receiving', 'ready', 'discarded')),
        files INTEGER NOT NULL DEFAULT 0,
        bytes INTEGER NOT NULL DEFAULT 0,
        manifest TEXT);                            -- JSON: [{"f": name on disk, "p": relative path, "n": bytes}],
                                                   -- written when the upload is finished (ready)
    CREATE INDEX uploads_profile ON uploads(profile_id);
    CREATE TABLE upload_files(                     -- the files of an upload still receiving, one row each so a
        upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,   -- folder of thousands doesn't
        name TEXT NOT NULL,                        -- rewrite the manifest each time; they become the manifest
        file TEXT NOT NULL,                        -- its random name on disk
        path TEXT NOT NULL,                        -- relative path, encrypted
        bytes INTEGER NOT NULL,
        PRIMARY KEY(upload_id, name));             -- name: a keyed hash of the path, so a path can't come twice
    """,
    # version 12: imports run in the background (jobs.py): one row per import of an upload, which the page polls.
    # message, warnings and summary say what was in the records, so they are encrypted (crypto.enc).
    """
    CREATE TABLE jobs(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        state TEXT NOT NULL CHECK(state IN ('queued', 'running', 'done', 'failed')),
        stage TEXT CHECK(stage IN ('detecting', 'parsing', 'saving')),   -- while running: how far it got
        importer TEXT,                             -- the importer's NAME: asked for, or detected
        message TEXT,                              -- why it failed, in plain language (the traceback is only logged)
        warnings TEXT,                             -- JSON list of the importer's warnings
        summary TEXT,                              -- JSON: counts by kind and the date range, once done
        import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,   -- once done
        created INTEGER NOT NULL,
        started INTEGER,
        finished INTEGER);
    CREATE INDEX jobs_profile ON jobs(profile_id, id);
    CREATE INDEX jobs_state ON jobs(state);
    """,
    # version 13: preview an import before saving it (jobs.py, #22). A job stops after parsing, in state "preview",
    # with the parsed records sealed in profiles/<id>/previews/<job id> until it is confirmed (back to the worker,
    # stage "saving") or discarded. Events and series get key_hash: a keyed hash (HMAC, records._key_secret) of
    # the importer's source key, so duplicates are found without decrypting every row; the key itself stays in the
    # encrypted body. '' means no key, NULL not worked out yet (records.backfill_key_hashes, at start-up).
    # SQLite can't change a CHECK, so jobs is made again with the new states.
    """
    ALTER TABLE events ADD COLUMN key_hash TEXT;
    ALTER TABLE series ADD COLUMN key_hash TEXT;
    CREATE INDEX events_key ON events(profile_id, key_hash);
    CREATE INDEX series_key ON series(profile_id, key_hash);
    CREATE TABLE jobs_13(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        state TEXT NOT NULL CHECK(state IN ('queued', 'running', 'preview', 'done', 'failed', 'discarded')),
        stage TEXT CHECK(stage IN ('detecting', 'parsing', 'saving')),   -- queued with "saving": confirmed, to save
        importer TEXT,
        message TEXT,
        warnings TEXT,
        summary TEXT,                              -- JSON: once previewed, what's new, the duplicates and the range;
                                                   -- once done, what was saved
        import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
        created INTEGER NOT NULL,
        started INTEGER,
        finished INTEGER,
        confirmed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- who said to save it (or discarded it)
        keep TEXT);                                -- JSON: the duplicates chosen to save anyway
    INSERT INTO jobs_13(id, profile_id, upload_id, created_by, state, stage, importer, message, warnings, summary,
                        import_id, created, started, finished)
        SELECT id, profile_id, upload_id, created_by, state, stage, importer, message, warnings, summary,
               import_id, created, started, finished FROM jobs;
    DROP TABLE jobs;
    ALTER TABLE jobs_13 RENAME TO jobs;
    CREATE INDEX jobs_profile ON jobs(profile_id, id);
    CREATE INDEX jobs_state ON jobs(state);
    CREATE INDEX jobs_upload ON jobs(upload_id);
    """,
    # version 14: import history with undo (#24). An undone import's row is deleted with everything it brought
    # (records.drop), so what the history showed for it is kept here, encrypted like any health detail: its
    # importer, file names and counts.
    """
    CREATE TABLE import_undos(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        import_id INTEGER NOT NULL,                -- no reference: the import is gone (and its id may come again)
        at INTEGER NOT NULL,                       -- when it was undone
        by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        by_name TEXT NOT NULL,                     -- the username then
        entry TEXT NOT NULL);                      -- JSON: the history entry as it was just before (history.py)
    CREATE INDEX import_undos_profile ON import_undos(profile_id);
    """,
    # version 15: corrections made in the app (edits.py). An edit belongs to the profile, never to an import, so
    # undoing or replacing an import keeps it; it points at an event by the event's source key (or, for events
    # without one, a key made from what the event says), so it finds the event again after a re-import gives it a
    # new id. target is a keyed hash of that key, for looking it up; the key itself and the value are encrypted.
    """
    CREATE TABLE edits(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        target TEXT NOT NULL,                      -- keyed hash (security.pepper_mac) of the event's key
        target_key TEXT NOT NULL,                  -- the key, encrypted
        action TEXT NOT NULL CHECK(action IN ('hide', 'rename', 'date', 'not_stay', 'merge')),
        other TEXT,                                -- merge: the keyed hash of the event folded into the target
        other_key TEXT,                            -- merge: its key, encrypted
        value TEXT,                                -- JSON, encrypted: the new title or time, and what the event was
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- NULL: the corrections.json migration
        at INTEGER NOT NULL);
    CREATE INDEX edits_target ON edits(profile_id, target);
    """,
    # version 16: lab names matched during import (labmap.py, #23). A lab spelled in a way lab-aliases.json doesn't
    # know, once matched to a series (or kept as its own) on an import's preview, is matched the same way in later
    # imports, on every profile of the install. lookup is a keyed hash (security.pepper_mac) of the importer, the
    # name as reported, its unit and LOINC code, all normalised; the name, unit and code themselves (source) and the
    # series it goes to (target) are encrypted. target NULL: kept as a series of its own. jobs.labs holds the choices
    # sent with a preview's confirm, encrypted, until the job saves them.
    """
    CREATE TABLE labmaps(
        id INTEGER PRIMARY KEY,
        lookup TEXT NOT NULL UNIQUE,
        importer TEXT NOT NULL,                    -- the importer's NAME ("manual" for hand entry)
        source TEXT NOT NULL,                      -- JSON {name, unit, loinc}, encrypted
        target TEXT,                               -- the series name, encrypted; NULL: keep as its own series
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,                -- the username then
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    ALTER TABLE jobs ADD COLUMN labs TEXT;
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
