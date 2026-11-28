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
    # version 17: where each record came from (#100). imports.synced: when a live connection (#91) last brought this
    # import up to date; NULL for an import that is read once (an upload, typed in, the setup build). The page shows
    # it beside each record's source; whatever sets it must bump profiles.records_rev, as any change to records does.
    """
    ALTER TABLE imports ADD COLUMN synced INTEGER;
    """,
    # version 18: settings in the database instead of a settings.json per profile (settings.py, #35). A row is one
    # setting's value for a profile (profile_id set), an account (user_id set) or the whole install (neither), as
    # the setting's definition in the registry says. A value that can hold health information (lab names, rules)
    # is encrypted in sealed; others are plain JSON in value. Each profile's settings.json is loaded at the next
    # start (settings.migrate_files); settings_seeded marks a profile whose settings were seeded (from its own
    # settings.json, or the setup build's on the first load), so a later build never refills one that was reset.
    """
    CREATE TABLE settings(
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL,                         -- the setting's key in the registry ("overview.keyLabs")
        profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        value TEXT,                                -- JSON, for a setting that holds no health information
        sealed TEXT,                               -- JSON, encrypted, for one that can
        changed INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- NULL: a migration, import or the setup build
        CHECK(profile_id IS NULL OR user_id IS NULL),
        CHECK((value IS NULL) != (sealed IS NULL)));
    CREATE UNIQUE INDEX settings_key ON settings(key, IFNULL(profile_id, 0), IFNULL(user_id, 0));
    CREATE INDEX settings_profile ON settings(profile_id);
    CREATE INDEX settings_user ON settings(user_id);
    ALTER TABLE profiles ADD COLUMN settings_seeded INTEGER NOT NULL DEFAULT 0;
    UPDATE profiles SET settings_seeded=1 WHERE build_mtime IS NOT NULL;   -- the build is loaded: its settings are in
                                                                           -- the profile's settings.json, migrated
    """,
    # version 19: connected sources (#32, #91; connections.py, sync.py). smart_clients holds what an admin set up
    # for each vendor under option 1 (docs/connected-sources-oauth.md): the app registered with it, its client ID
    # and secret (encrypted), the scopes to ask for. connections is one per profile and source: the tokens, refresh
    # token or private key it signs in with, encrypted, its state and its sync times. connect_pending holds a
    # sign-in between leaving for the vendor and coming back: a hash of its state, bound to the session that started
    # it, with the PKCE verifier encrypted. sync_changes is what a scheduled sync changed in place (an event, or an
    # entry of an import's profile-wide lists), with what it was before, so undoing the sync puts it back.
    """
    CREATE TABLE smart_clients(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,                        -- what people see ("Epic sandbox", "Riverside Health")
        vendor TEXT NOT NULL,                      -- which setup guide: epic, oracle, other
        issuer TEXT NOT NULL UNIQUE,               -- the FHIR base: the issuer, and the aud sent at sign-in
        client_id TEXT NOT NULL,                   -- from the vendor's developer site
        secret TEXT,                               -- the client secret, encrypted; NULL: a public client
        auth TEXT NOT NULL CHECK(auth IN ('public', 'confidential', 'dynamic')),   -- dynamic: Epic's registration
        scopes TEXT NOT NULL,                      -- asked for at sign-in, space-separated
        discovery TEXT,                            -- JSON: .well-known/smart-configuration as last read (public)
        tested INTEGER,                            -- when discovery last worked
        created INTEGER NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed INTEGER);
    CREATE TABLE connections(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        client_id INTEGER REFERENCES smart_clients(id) ON DELETE SET NULL,
        source TEXT NOT NULL,                      -- the vendor setting's name when it connected
        issuer TEXT NOT NULL,
        fhir_base TEXT NOT NULL,
        patient TEXT,                              -- the patient's id at the vendor, encrypted
        credential TEXT CHECK(credential IN ('refresh', 'jwt', 'access')),   -- how it gets new access tokens
        secret TEXT,                               -- JSON, encrypted: the access token, the refresh token or the
                                                   -- private key and dynamic client id; NULL once disconnected
        scopes TEXT,                               -- as granted
        expires INTEGER,                           -- when the access token runs out
        state TEXT NOT NULL CHECK(state IN ('active', 'needs reconnect', 'disconnected')),
        cursor TEXT,                               -- JSON {type: _lastUpdated to ask after}; NULL until the first
                                                   -- pull is saved
        pending_cursor TEXT,                       -- the first pull's, which becomes the cursor when it's saved
        first_job INTEGER REFERENCES jobs(id) ON DELETE SET NULL,   -- the first pull, previewed like an upload
        last_sync INTEGER,                         -- the last pull that worked
        next_sync INTEGER,
        failures INTEGER NOT NULL DEFAULT 0,       -- pulls failed in a row, for the backoff
        error_kind TEXT,                           -- unavailable (try later), access (reconnect), other
        last_error TEXT,                           -- encrypted
        report TEXT,                               -- JSON, encrypted: the last pull, type by type
        created INTEGER NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        disconnected INTEGER);
    CREATE INDEX connections_profile ON connections(profile_id);
    CREATE INDEX connections_due ON connections(state, next_sync);
    CREATE TABLE connect_pending(
        state_hash TEXT PRIMARY KEY,               -- SHA-256 of state; state itself is only in the browser's URL
        session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        client_id INTEGER NOT NULL REFERENCES smart_clients(id) ON DELETE CASCADE,
        connection_id INTEGER REFERENCES connections(id) ON DELETE CASCADE,   -- set: reconnecting this one
        verifier TEXT NOT NULL,                    -- the PKCE code_verifier, encrypted
        redirect_uri TEXT NOT NULL,
        scopes TEXT NOT NULL,
        token_url TEXT NOT NULL,                   -- from discovery at the start: the code goes nowhere else
        expires INTEGER NOT NULL);
    ALTER TABLE imports ADD COLUMN connection_id INTEGER REFERENCES connections(id) ON DELETE SET NULL;
    CREATE INDEX imports_connection ON imports(connection_id);
    CREATE TABLE sync_changes(
        id INTEGER PRIMARY KEY,
        import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,   -- the sync that made the change
        event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,             -- an event updated in place...
        meta_import INTEGER REFERENCES imports(id) ON DELETE CASCADE,         -- ...or an entry of this import's meta
        previous TEXT NOT NULL,                    -- JSON, encrypted: what it was before
        CHECK((event_id IS NULL) != (meta_import IS NULL)));
    CREATE INDEX sync_changes_import ON sync_changes(import_id);
    CREATE INDEX sync_changes_event ON sync_changes(event_id);
    CREATE INDEX sync_changes_meta ON sync_changes(meta_import);
    """,
    # version 20: outside contacts (#97, #59; outside.py, net.py). One row per profile, purpose and host Sanguity's
    # server has contacted, with when it first and last did and how often, for the outside-connections view (the
    # activity log has each contact too, but is pruned and its details are encrypted). The host is encrypted like
    # an activity detail (a Health Link's host can say where someone is treated); the purpose is one of a few fixed
    # phrases. profile_id NULL: no profile involved (an admin's "Test this connection").
    """
    CREATE TABLE outside_contacts(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
        purpose TEXT NOT NULL,                     -- "Connected source: sync", "SMART Health Link", ...
        host TEXT NOT NULL,                        -- host[:port], encrypted; never a path or query
        first INTEGER NOT NULL,
        last INTEGER NOT NULL,
        count INTEGER NOT NULL DEFAULT 1);
    CREATE INDEX outside_contacts_profile ON outside_contacts(profile_id, purpose);
    """,
    # version 21: what a connection pulls and how it warns (#99, #101, #102; connections.py, sync.py). kinds and
    # since are the person's choice of records (NULL: every kind the access allows, from the beginning), also asked
    # at sign-in (connect_pending). attach is the connection's own attachment rules over the install's (settings
    # sync.*), attach_bytes what it has fetched so far, for its budget. warned is when its "stopped syncing" warning
    # went into the activity log, so it goes in once. sync_skipped lists the attachments a pull left out, for "fetch
    # anyway"; sync_removed keeps the rows "remove the records of a kind" took out, so undoing it from the import
    # history puts them back; imports.note says what such an import did.
    """
    ALTER TABLE connections ADD COLUMN kinds TEXT;          -- JSON list of kinds (connections.KINDS); NULL: all allowed
    ALTER TABLE connections ADD COLUMN since TEXT;          -- YYYY-MM-DD: records from then on; NULL: everything
    ALTER TABLE connections ADD COLUMN attach TEXT;         -- JSON {auto, max_mb, types, budget_mb}; NULL: install's
    ALTER TABLE connections ADD COLUMN attach_bytes INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE connections ADD COLUMN warned INTEGER;
    ALTER TABLE connect_pending ADD COLUMN kinds TEXT;
    ALTER TABLE connect_pending ADD COLUMN since TEXT;
    CREATE TABLE sync_skipped(
        id INTEGER PRIMARY KEY,
        connection_id INTEGER NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        ref_hash TEXT NOT NULL,                    -- SHA-256 of the attachment's address: listed once
        detail TEXT NOT NULL,                      -- JSON, encrypted: address, record, title, type, size, why
        found INTEGER NOT NULL,
        import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL);   -- set: fetched anyway, by that import
    CREATE UNIQUE INDEX sync_skipped_ref ON sync_skipped(connection_id, ref_hash);
    CREATE INDEX sync_skipped_profile ON sync_skipped(profile_id);
    CREATE TABLE sync_removed(
        id INTEGER PRIMARY KEY,
        import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,   -- the removal, in the history
        home_import INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE, -- where the row was
        what TEXT NOT NULL CHECK(what IN ('event', 'series', 'meta')),
        row TEXT NOT NULL);                        -- JSON, encrypted: the row (a series with its results; a meta
                                                   -- entry with its list's path)
    CREATE INDEX sync_removed_import ON sync_removed(import_id);
    ALTER TABLE imports ADD COLUMN note TEXT;     -- JSON, encrypted: what a sync import that isn't a pull did (a
                                                  -- kind's records removed, an attachment fetched), for the history
    """,
    # version 22: the same lab result from two sources (#96, crossmatch.py). same_results is the person's decision on
    # a pair of results the rule flagged: "same result" (shown once) or "not the same" (not flagged again). Like
    # edits it belongs to the profile, not to an import, and finds its results by their point keys (encrypted), so
    # it survives re-imports and undo; pair is a keyed hash of both keys, for finding a pair without decrypting.
    # jobs.same: the preview's possible matches the person confirmed, linked when the import is saved.
    """
    CREATE TABLE same_results(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        pair TEXT NOT NULL,                        -- HMAC of both point keys (crossmatch.pair_hash)
        a_key TEXT NOT NULL,                       -- the two point keys, encrypted
        b_key TEXT NOT NULL,
        same INTEGER NOT NULL CHECK(same IN (0, 1)),   -- 1 same result, 0 not the same
        value TEXT,                                -- JSON, encrypted: what each side was when decided
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        at INTEGER NOT NULL,
        UNIQUE(profile_id, pair));
    ALTER TABLE jobs ADD COLUMN same TEXT;         -- JSON: refs of the preview's possible matches confirmed
    """,
    # version 23: calendar feeds (#51; calendar.py). One per person and profile: the address their calendar app
    # fetches carries a random token, of which only the SHA-256 is kept. Fetches aren't logged, only the last one's
    # time; making, rotating and revoking a feed are (activity "calendar").
    """
    CREATE TABLE calendar_feeds(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,           -- SHA-256 of the token in the address; the token is never stored
        details TEXT NOT NULL DEFAULT 'minimal' CHECK(details IN ('minimal', 'full')),
        labs INTEGER NOT NULL DEFAULT 1,           -- 1: also expected lab draws (a guess from their rhythm)
        tz TEXT,                                   -- the zone of the browser that set it up (records' times are local)
        created INTEGER NOT NULL,
        rotated INTEGER,                           -- when the token was last replaced
        last_fetched INTEGER,                      -- the calendar app's last fetch (to the minute)
        UNIQUE(profile_id, user_id));
    CREATE INDEX calendar_feeds_user ON calendar_feeds(user_id);
    """,
    # version 24: the journal (journal.py, #49): dated notes, symptoms and side effects written by the people who use
    # the profile. Like edits, an entry belongs to the profile, never to an import, so undoing or replacing imports
    # leaves it. Everything it says (kind, date, what, severity, note, the medication it's linked to) is in body,
    # encrypted; only who wrote or last changed it, and when, are plain.
    """
    CREATE TABLE journal(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,                        -- JSON, encrypted: {kind, t, title, severity, note, med}
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,                -- the username then
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    CREATE INDEX journal_profile ON journal(profile_id);
    """,
    # version 25: an import's own name (#38): what the person calls it in place of the importer's label ("C-CDA health
    # summary" -> "Dr. Rivera's clinic"), shown wherever the records say where they came from. NULL: the label.
    """
    ALTER TABLE imports ADD COLUMN name TEXT;     -- encrypted
    """,
    # version 26: share links (#47; share_links.py). A read-only view of part of a profile for someone without an
    # account: only the SHA-256 of the link's token is kept. It works until it expires or is revoked, and only while
    # the owner who made it is still an owner. Every view is logged (activity "link"); views and last_viewed count them.
    """
    CREATE TABLE share_links(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- NULL: the account is gone, so the link is too
        created_name TEXT NOT NULL,                -- the username then
        token_hash TEXT NOT NULL UNIQUE,           -- SHA-256 of the token in the link; the token is never stored
        label TEXT,                                -- encrypted: what the owner calls it ("Dr. Rivera")
        scope TEXT NOT NULL,                       -- JSON, encrypted: {sections, labs, from, to}
        created INTEGER NOT NULL,
        expires INTEGER NOT NULL,
        revoked INTEGER,                           -- when it was revoked; NULL while it isn't
        revoked_name TEXT,                         -- by whom (username then)
        views INTEGER NOT NULL DEFAULT 0,
        last_viewed INTEGER);
    CREATE INDEX share_links_profile ON share_links(profile_id);
    """,
    # version 27: what people said about out-of-pocket costs on the Costs tab (oop.py, #55): a payment or a claim's
    # share tagged HSA or FSA, a payment matched to a claim (or to none), two payments that look alike said to be two.
    # Like edits, a mark belongs to the profile, never to an import, and finds its payment or claim by key.
    """
    CREATE TABLE cost_marks(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        target TEXT NOT NULL,                      -- keyed hash (security.pepper_mac) of the payment's or claim's key
        target_key TEXT NOT NULL,                  -- the key, encrypted
        value TEXT NOT NULL,                       -- JSON, encrypted: {hsa?, claim?, same?}
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        at INTEGER NOT NULL,
        UNIQUE(profile_id, target));
    """,
    # version 28: backups made and checked from the Admin page (backup_jobs.py, #200): Back up now, the schedule's
    # backups and Check a backup, one row each, run in turn in the background; the page polls the one running. The
    # backups themselves are the files in BACKUP_DIR; these rows say who asked, how it went and what a check found.
    # Nothing here is health information (names of backup files, sizes, row counts), so nothing is encrypted.
    """
    CREATE TABLE backup_runs(
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('backup', 'check')),
        origin TEXT NOT NULL CHECK(origin IN ('manual', 'schedule')),
        state TEXT NOT NULL CHECK(state IN ('queued', 'running', 'done', 'failed')),
        file TEXT,                                 -- the backup's file name in BACKUP_DIR: made, or checked
        size INTEGER,                              -- bytes, once made
        stage TEXT, done INTEGER, total INTEGER,   -- how far it has got, while running
        ok INTEGER,                                -- a check: 1 the backup is good, 0 it isn't
        message TEXT,                              -- why it failed, or what a check found wrong
        result TEXT,                               -- JSON: a check's counts, or the older backups a backup removed
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,                -- the username then, or "scheduled backup"
        created INTEGER NOT NULL,
        started INTEGER,
        finished INTEGER);
    """,
    # version 29: a connected source waiting for its vendor's review (#165; connections.py): when an admin said so,
    # NULL once the review is done. Nobody can connect it meanwhile, its client ID may still be empty (''), and
    # Portals and health plans tells people to upload the portal's export instead.
    """
    ALTER TABLE smart_clients ADD COLUMN waiting INTEGER;
    """,
    # version 30: About this person (#194; person.py). The birth date and sex #44 kept as the settings ranges.birthDate
    # and ranges.sex move to person.birthDate and person.sex, for every profile, once: renamed in place, so the sealed
    # values (encrypted, not bound to the key's name) stay as they are and #44 reads them from there. Nothing to create:
    # About this person is three profile settings (person.heights is new). A row whose new name is somehow taken is
    # left to the newer one and removed.
    """
    UPDATE OR IGNORE settings SET key='person.birthDate' WHERE key='ranges.birthDate';
    UPDATE OR IGNORE settings SET key='person.sex' WHERE key='ranges.sex';
    DELETE FROM settings WHERE key IN ('ranges.birthDate', 'ranges.sex');
    """,
    # version 31: the visit summary's questions draft per person (#219; summary.py). It was one per profile (the
    # settings key summary.questions), so a viewer's edit replaced an owner's (docs/security-review.md, A4). Now one row
    # per account and profile, encrypted, gone with the account, the profile or the account's access to it. Each shared
    # draft moves once: to whoever kept it last, and, unless that was a viewer, to every owner and editor too, who saw it
    # before; a viewer never gets someone else's. The sealed value is copied as it is (encrypted, not bound to its row).
    """
    CREATE TABLE summary_drafts(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        sealed TEXT NOT NULL,                      -- JSON text, encrypted
        changed INTEGER NOT NULL,
        UNIQUE(profile_id, user_id));
    CREATE INDEX summary_drafts_user ON summary_drafts(user_id);
    CREATE TRIGGER summary_drafts_unshared AFTER DELETE ON memberships BEGIN
        DELETE FROM summary_drafts WHERE profile_id=OLD.profile_id AND user_id=OLD.user_id;
    END;
    INSERT OR IGNORE INTO summary_drafts(profile_id, user_id, sealed, changed)
        SELECT s.profile_id, m.user_id, s.sealed, s.changed FROM settings s JOIN memberships m ON m.profile_id = s.profile_id
        WHERE s.key = 'summary.questions' AND s.user_id IS NULL AND s.sealed IS NOT NULL
          AND (m.user_id = s.changed_by
               OR (m.role IN ('owner', 'editor') AND NOT EXISTS (SELECT 1 FROM memberships w WHERE w.profile_id = s.profile_id
                                                                 AND w.user_id = s.changed_by AND w.role = 'viewer')));
    DELETE FROM settings WHERE key = 'summary.questions';
    """,

    # version 32: photos attached to journal entries (#161; journal_photos.py). The pictures are sealed files in
    # profiles/<id>/journal/ under random names; a row says which entry a photo belongs to (NULL: sent, its entry not
    # saved yet), its type and size. Nothing here says what a photo shows: its description is in the entry's encrypted
    # body. Deleting the entry deletes its photos (journal_photos.drop_entry removes the files, the cascade the rows).
    """
    CREATE TABLE journal_photos(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        entry_id INTEGER REFERENCES journal(id) ON DELETE CASCADE,
        file TEXT NOT NULL,                        -- its random name in profiles/<id>/journal/
        type TEXT NOT NULL CHECK(type IN ('jpeg', 'png', 'webp')),
        bytes INTEGER NOT NULL,
        created INTEGER NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL);
    CREATE INDEX journal_photos_profile ON journal_photos(profile_id, entry_id);
    """,

    # version 33 (#162, #235): medication reminders through the calendar feed (med_reminders.py). A
    # reminder belongs to the profile, never to an import, and finds its medication by the medication's grouping key
    # (medications.med_key), so undoing or re-importing the records keeps it. What it says (the medication, times of
    # day, days, from, until, time zone) is in body, encrypted; whether it's on, who set it and when are plain.
    # calendar_feeds.meds: whether a person's feed lists the profile's reminders. med_doses (#235, med_log.py): the
    # medication log, doses the person says were taken, taken late or skipped; the profile's like the journal, its body
    # (the medication, when, what happened, a note, the reminder time) encrypted.
    """
    CREATE TABLE med_reminders(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,                        -- JSON, encrypted: {med, name, times, days, from, until, tz}
        active INTEGER NOT NULL DEFAULT 1,         -- 0: turned off (kept, listed nowhere)
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,                -- the username then
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    CREATE INDEX med_reminders_profile ON med_reminders(profile_id);
    ALTER TABLE calendar_feeds ADD COLUMN meds INTEGER NOT NULL DEFAULT 1;   -- 1: also the medication reminders
    CREATE TABLE med_doses(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,                        -- JSON, encrypted: {med, name, t, status, note, slot}
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,                -- the username then
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    CREATE INDEX med_doses_profile ON med_doses(profile_id);
    """,
    # version 34: an entry moved to another category, and rules that keep doing it for a source's new imports (#237;
    # edits.py). SQLite can't change a CHECK, so the edits table is made again with the two new actions, every edit
    # copied as it is.
    """
    CREATE TABLE edits_34(
        id INTEGER PRIMARY KEY,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        target TEXT NOT NULL,                      -- keyed hash (security.pepper_mac) of the event's key (a rule's key)
        target_key TEXT NOT NULL,                  -- the key, encrypted
        action TEXT NOT NULL CHECK(action IN ('hide', 'rename', 'date', 'not_stay', 'merge', 'kind', 'kind_rule')),
        other TEXT,                                -- merge: the keyed hash of the event folded into the target
        other_key TEXT,                            -- merge: its key, encrypted
        value TEXT,                                -- JSON, encrypted: the new title, time or kind, and what it was
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- NULL: the corrections.json migration
        at INTEGER NOT NULL);
    INSERT INTO edits_34(id, profile_id, target, target_key, action, other, other_key, value, created_by, at)
        SELECT id, profile_id, target, target_key, action, other, other_key, value, created_by, at FROM edits;
    DROP TABLE edits;
    ALTER TABLE edits_34 RENAME TO edits;
    CREATE INDEX edits_target ON edits(profile_id, target);
    """,
    # version 35: who typed an import's records in (#325; routes_manual.py), on the import itself. A typed-in save has
    # no job, whose created_by names an upload's account; NULL for every other origin, for imports typed in before
    # this, and once the account is removed.
    """
    ALTER TABLE imports ADD COLUMN created_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
    """,
    # version 36: a lab name's match forgotten with the import that made it (#500; labmap.py). import_id: the import
    # whose review made the choice (NULL for those made before this, and once that import is gone); used: when another
    # import was last saved with it. Undoing the import forgets a choice no other import has used and no admin changed.
    """
    ALTER TABLE labmaps ADD COLUMN import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL;
    ALTER TABLE labmaps ADD COLUMN used INTEGER;
    CREATE INDEX labmaps_import ON labmaps(import_id);
    """,
    # version 37: an import's id never comes again (#512). Without AUTOINCREMENT, the next import after undoing the
    # newest took its id, which the undone one keeps in import_undos and the page still finds it by (a toast's Undo).
    # SQLite can't add AUTOINCREMENT to a table, so imports is made again, its rows copied as they are (the tables that
    # point at it keep pointing at the same ids: steps run with foreign keys off, init), and the count starts after the
    # highest id it has had, undone ones included.
    """
    CREATE TABLE imports_37(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        at INTEGER NOT NULL,
        origin TEXT NOT NULL,                      -- build | dataset | upload | manual | sync (version 10 says each)
        format TEXT NOT NULL,                      -- the reader that produced it ("sanguity-data": a data.json)
        meta TEXT,                                 -- JSON: when it was built, its source, the profile-wide lists
        synced INTEGER,                            -- (version 17)
        connection_id INTEGER REFERENCES connections(id) ON DELETE SET NULL,   -- (version 19)
        note TEXT,                                 -- (version 21)
        name TEXT,                                 -- (version 25)
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL);   -- (version 35)
    INSERT INTO imports_37(id, profile_id, at, origin, format, meta, synced, connection_id, note, name, created_by)
        SELECT id, profile_id, at, origin, format, meta, synced, connection_id, note, name, created_by FROM imports;
    DROP TABLE imports;
    ALTER TABLE imports_37 RENAME TO imports;
    CREATE INDEX imports_profile ON imports(profile_id);
    CREATE INDEX imports_connection ON imports(connection_id);
    INSERT INTO sqlite_sequence(name, seq) SELECT 'imports', 0 WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name='imports');
    UPDATE sqlite_sequence SET seq = MAX(seq, (SELECT COALESCE(MAX(id), 0) FROM imports),
                                         (SELECT COALESCE(MAX(import_id), 0) FROM import_undos)) WHERE name='imports';
    """,
    # version 38: lab-name matches to check (#569; labmap.py). checked: when the match was chosen by hand (a review's
    # choice made since, or an admin's Keep or Change). NULL for those remembered before this, when a review could
    # fill in a look-alike name as sure (#546): one #546's rule wouldn't make is then not applied until an admin keeps it.
    # One an admin had changed already is the admin's own, so checked then (#575).
    """
    ALTER TABLE labmaps ADD COLUMN checked INTEGER;
    UPDATE labmaps SET checked = changed WHERE changed IS NOT NULL;
    """,
    # version 39: ids that never come again (#588). Without AUTOINCREMENT, the newest row's id, once deleted, went to the
    # next row made while a page, a file or another table still held it: a deleted profile's phone data (the receiver's
    # slot is its id) showed in the next profile made, a page loaded before an undo hid or renamed the record the next
    # import brought, a stale page's save or delete changed another journal entry, dose, reminder or edit, and the next
    # sign-in after one signed out had its views taken as logged already. Each table whose id leaves it is made again as
    # version 37 made imports: its rows copied as they are (foreign keys off, db.upgrade), its indexes again, and its
    # count started after the highest id it has, a signed-out session the activity log names included. A profile's
    # count also skips any id a receiver slot or folder was left on disk for (profiles.create_profile).
    """
    CREATE TABLE users_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        pw_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        must_change_pw INTEGER NOT NULL DEFAULT 0,
        created INTEGER NOT NULL,
        totp_secret TEXT,
        totp_pending TEXT,
        totp_last_step INTEGER NOT NULL DEFAULT 0,
        pw_short INTEGER NOT NULL DEFAULT 0,
        webauthn_handle TEXT,
        temp_pw_expires INTEGER);
    INSERT INTO users_39(id, username, pw_hash, is_admin, disabled, must_change_pw, created, totp_secret,
        totp_pending, totp_last_step, pw_short, webauthn_handle, temp_pw_expires)
        SELECT id, username, pw_hash, is_admin, disabled, must_change_pw, created, totp_secret, totp_pending,
            totp_last_step, pw_short, webauthn_handle, temp_pw_expires FROM users;
    DROP TABLE users;
    ALTER TABLE users_39 RENAME TO users;
    CREATE TABLE profiles_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        created INTEGER NOT NULL,
        linked_build INTEGER NOT NULL DEFAULT 0,
        build_mtime,
        watch_file TEXT,
        records_rev INTEGER NOT NULL DEFAULT 0,
        settings_seeded INTEGER NOT NULL DEFAULT 0);
    INSERT INTO profiles_39(id, name, created, linked_build, build_mtime, watch_file, records_rev, settings_seeded)
        SELECT id, name, created, linked_build, build_mtime, watch_file, records_rev, settings_seeded FROM profiles;
    DROP TABLE profiles;
    ALTER TABLE profiles_39 RENAME TO profiles;
    CREATE TABLE sessions_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        expires INTEGER NOT NULL,
        ip TEXT,
        agent TEXT,
        verified_at INTEGER);
    INSERT INTO sessions_39(id, token_hash, user_id, created, last_seen, expires, ip, agent, verified_at)
        SELECT id, token_hash, user_id, created, last_seen, expires, ip, agent, verified_at FROM sessions;
    DROP TABLE sessions;
    ALTER TABLE sessions_39 RENAME TO sessions;
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE passkeys_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        credential_id TEXT NOT NULL UNIQUE,
        public_key BLOB NOT NULL,
        sign_count INTEGER NOT NULL DEFAULT 0,
        rp_id TEXT NOT NULL,
        name TEXT NOT NULL,
        transports TEXT,
        created INTEGER NOT NULL,
        last_used INTEGER);
    INSERT INTO passkeys_39(id, user_id, credential_id, public_key, sign_count, rp_id, name, transports, created,
        last_used)
        SELECT id, user_id, credential_id, public_key, sign_count, rp_id, name, transports, created, last_used FROM
            passkeys;
    DROP TABLE passkeys;
    ALTER TABLE passkeys_39 RENAME TO passkeys;
    CREATE INDEX passkeys_user ON passkeys(user_id);
    CREATE TABLE uploads_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        created INTEGER NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        state TEXT NOT NULL CHECK(state IN ('receiving', 'ready', 'discarded')),
        files INTEGER NOT NULL DEFAULT 0,
        bytes INTEGER NOT NULL DEFAULT 0,
        manifest TEXT);
    INSERT INTO uploads_39(id, profile_id, created, created_by, state, files, bytes, manifest)
        SELECT id, profile_id, created, created_by, state, files, bytes, manifest FROM uploads;
    DROP TABLE uploads;
    ALTER TABLE uploads_39 RENAME TO uploads;
    CREATE INDEX uploads_profile ON uploads(profile_id);
    CREATE TABLE jobs_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        state TEXT NOT NULL CHECK(state IN ('queued', 'running', 'preview', 'done', 'failed', 'discarded')),
        stage TEXT CHECK(stage IN ('detecting', 'parsing', 'saving')),
        importer TEXT,
        message TEXT,
        warnings TEXT,
        summary TEXT,
        import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
        created INTEGER NOT NULL,
        started INTEGER,
        finished INTEGER,
        confirmed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        keep TEXT,
        labs TEXT,
        same TEXT);
    INSERT INTO jobs_39(id, profile_id, upload_id, created_by, state, stage, importer, message, warnings, summary,
        import_id, created, started, finished, confirmed_by, keep, labs, same)
        SELECT id, profile_id, upload_id, created_by, state, stage, importer, message, warnings, summary, import_id,
            created, started, finished, confirmed_by, keep, labs, same FROM jobs;
    DROP TABLE jobs;
    ALTER TABLE jobs_39 RENAME TO jobs;
    CREATE INDEX jobs_profile ON jobs(profile_id, id);
    CREATE INDEX jobs_state ON jobs(state);
    CREATE INDEX jobs_upload ON jobs(upload_id);
    CREATE TABLE events_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
        body TEXT NOT NULL,
        detail TEXT,
        cost TEXT,
        cost_seq INTEGER,
        key_hash TEXT);
    INSERT INTO events_39(id, profile_id, import_id, body, detail, cost, cost_seq, key_hash)
        SELECT id, profile_id, import_id, body, detail, cost, cost_seq, key_hash FROM events;
    DROP TABLE events;
    ALTER TABLE events_39 RENAME TO events;
    CREATE INDEX events_profile ON events(profile_id);
    CREATE INDEX events_import ON events(import_id);
    CREATE INDEX events_key ON events(profile_id, key_hash);
    CREATE TABLE series_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
        body TEXT NOT NULL,
        key_hash TEXT);
    INSERT INTO series_39(id, profile_id, import_id, body, key_hash)
        SELECT id, profile_id, import_id, body, key_hash FROM series;
    DROP TABLE series;
    ALTER TABLE series_39 RENAME TO series;
    CREATE INDEX series_profile ON series(profile_id);
    CREATE INDEX series_import ON series(import_id);
    CREATE INDEX series_key ON series(profile_id, key_hash);
    CREATE TABLE edits_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        target TEXT NOT NULL,
        target_key TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('hide', 'rename', 'date', 'not_stay', 'merge', 'kind', 'kind_rule')),
        other TEXT,
        other_key TEXT,
        value TEXT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        at INTEGER NOT NULL);
    INSERT INTO edits_39(id, profile_id, target, target_key, action, other, other_key, value, created_by, at)
        SELECT id, profile_id, target, target_key, action, other, other_key, value, created_by, at FROM edits;
    DROP TABLE edits;
    ALTER TABLE edits_39 RENAME TO edits;
    CREATE INDEX edits_target ON edits(profile_id, target);
    CREATE TABLE labmaps_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lookup TEXT NOT NULL UNIQUE,
        importer TEXT NOT NULL,
        source TEXT NOT NULL,
        target TEXT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER,
        import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
        used INTEGER,
        checked INTEGER);
    INSERT INTO labmaps_39(id, lookup, importer, source, target, created_by, created_name, created, changed_by,
        changed_name, changed, import_id, used, checked)
        SELECT id, lookup, importer, source, target, created_by, created_name, created, changed_by, changed_name,
            changed, import_id, used, checked FROM labmaps;
    DROP TABLE labmaps;
    ALTER TABLE labmaps_39 RENAME TO labmaps;
    CREATE INDEX labmaps_import ON labmaps(import_id);
    CREATE TABLE smart_clients_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        vendor TEXT NOT NULL,
        issuer TEXT NOT NULL UNIQUE,
        client_id TEXT NOT NULL,
        secret TEXT,
        auth TEXT NOT NULL CHECK(auth IN ('public', 'confidential', 'dynamic')),
        scopes TEXT NOT NULL,
        discovery TEXT,
        tested INTEGER,
        created INTEGER NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed INTEGER,
        waiting INTEGER);
    INSERT INTO smart_clients_39(id, name, vendor, issuer, client_id, secret, auth, scopes, discovery, tested,
        created, created_by, changed, waiting)
        SELECT id, name, vendor, issuer, client_id, secret, auth, scopes, discovery, tested, created, created_by,
            changed, waiting FROM smart_clients;
    DROP TABLE smart_clients;
    ALTER TABLE smart_clients_39 RENAME TO smart_clients;
    CREATE TABLE connections_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        client_id INTEGER REFERENCES smart_clients(id) ON DELETE SET NULL,
        source TEXT NOT NULL,
        issuer TEXT NOT NULL,
        fhir_base TEXT NOT NULL,
        patient TEXT,
        credential TEXT CHECK(credential IN ('refresh', 'jwt', 'access')),
        secret TEXT,
        scopes TEXT,
        expires INTEGER,
        state TEXT NOT NULL CHECK(state IN ('active', 'needs reconnect', 'disconnected')),
        cursor TEXT,
        pending_cursor TEXT,
        first_job INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
        last_sync INTEGER,
        next_sync INTEGER,
        failures INTEGER NOT NULL DEFAULT 0,
        error_kind TEXT,
        last_error TEXT,
        report TEXT,
        created INTEGER NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        disconnected INTEGER,
        kinds TEXT,
        since TEXT,
        attach TEXT,
        attach_bytes INTEGER NOT NULL DEFAULT 0,
        warned INTEGER);
    INSERT INTO connections_39(id, profile_id, client_id, source, issuer, fhir_base, patient, credential, secret,
        scopes, expires, state, cursor, pending_cursor, first_job, last_sync, next_sync, failures, error_kind,
        last_error, report, created, created_by, disconnected, kinds, since, attach, attach_bytes, warned)
        SELECT id, profile_id, client_id, source, issuer, fhir_base, patient, credential, secret, scopes, expires,
            state, cursor, pending_cursor, first_job, last_sync, next_sync, failures, error_kind, last_error, report,
            created, created_by, disconnected, kinds, since, attach, attach_bytes, warned FROM connections;
    DROP TABLE connections;
    ALTER TABLE connections_39 RENAME TO connections;
    CREATE INDEX connections_profile ON connections(profile_id);
    CREATE INDEX connections_due ON connections(state, next_sync);
    CREATE TABLE same_results_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        pair TEXT NOT NULL,
        a_key TEXT NOT NULL,
        b_key TEXT NOT NULL,
        same INTEGER NOT NULL CHECK(same IN (0, 1)),
        value TEXT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        at INTEGER NOT NULL,
        UNIQUE(profile_id, pair));
    INSERT INTO same_results_39(id, profile_id, pair, a_key, b_key, same, value, created_by, at)
        SELECT id, profile_id, pair, a_key, b_key, same, value, created_by, at FROM same_results;
    DROP TABLE same_results;
    ALTER TABLE same_results_39 RENAME TO same_results;
    CREATE TABLE calendar_feeds_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        details TEXT NOT NULL DEFAULT 'minimal' CHECK(details IN ('minimal', 'full')),
        labs INTEGER NOT NULL DEFAULT 1,
        tz TEXT,
        created INTEGER NOT NULL,
        rotated INTEGER,
        last_fetched INTEGER,
        meds INTEGER NOT NULL DEFAULT 1,
        UNIQUE(profile_id, user_id));
    INSERT INTO calendar_feeds_39(id, profile_id, user_id, token_hash, details, labs, tz, created, rotated,
        last_fetched, meds)
        SELECT id, profile_id, user_id, token_hash, details, labs, tz, created, rotated, last_fetched, meds FROM
            calendar_feeds;
    DROP TABLE calendar_feeds;
    ALTER TABLE calendar_feeds_39 RENAME TO calendar_feeds;
    CREATE INDEX calendar_feeds_user ON calendar_feeds(user_id);
    CREATE TABLE journal_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    INSERT INTO journal_39(id, profile_id, body, created_by, created_name, created, changed_by, changed_name, changed)
        SELECT id, profile_id, body, created_by, created_name, created, changed_by, changed_name, changed FROM journal;
    DROP TABLE journal;
    ALTER TABLE journal_39 RENAME TO journal;
    CREATE INDEX journal_profile ON journal(profile_id);
    CREATE TABLE share_links_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        label TEXT,
        scope TEXT NOT NULL,
        created INTEGER NOT NULL,
        expires INTEGER NOT NULL,
        revoked INTEGER,
        revoked_name TEXT,
        views INTEGER NOT NULL DEFAULT 0,
        last_viewed INTEGER);
    INSERT INTO share_links_39(id, profile_id, created_by, created_name, token_hash, label, scope, created, expires,
        revoked, revoked_name, views, last_viewed)
        SELECT id, profile_id, created_by, created_name, token_hash, label, scope, created, expires, revoked,
            revoked_name, views, last_viewed FROM share_links;
    DROP TABLE share_links;
    ALTER TABLE share_links_39 RENAME TO share_links;
    CREATE INDEX share_links_profile ON share_links(profile_id);
    CREATE TABLE journal_photos_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        entry_id INTEGER REFERENCES journal(id) ON DELETE CASCADE,
        file TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('jpeg', 'png', 'webp')),
        bytes INTEGER NOT NULL,
        created INTEGER NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL);
    INSERT INTO journal_photos_39(id, profile_id, entry_id, file, type, bytes, created, created_by)
        SELECT id, profile_id, entry_id, file, type, bytes, created, created_by FROM journal_photos;
    DROP TABLE journal_photos;
    ALTER TABLE journal_photos_39 RENAME TO journal_photos;
    CREATE INDEX journal_photos_profile ON journal_photos(profile_id, entry_id);
    CREATE TABLE med_reminders_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    INSERT INTO med_reminders_39(id, profile_id, body, active, created_by, created_name, created, changed_by,
        changed_name, changed)
        SELECT id, profile_id, body, active, created_by, created_name, created, changed_by, changed_name, changed
            FROM med_reminders;
    DROP TABLE med_reminders;
    ALTER TABLE med_reminders_39 RENAME TO med_reminders;
    CREATE INDEX med_reminders_profile ON med_reminders(profile_id);
    CREATE TABLE med_doses_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    INSERT INTO med_doses_39(id, profile_id, body, created_by, created_name, created, changed_by, changed_name, changed)
        SELECT id, profile_id, body, created_by, created_name, created, changed_by, changed_name, changed FROM
            med_doses;
    DROP TABLE med_doses;
    ALTER TABLE med_doses_39 RENAME TO med_doses;
    CREATE INDEX med_doses_profile ON med_doses(profile_id);
    CREATE TABLE sync_skipped_39(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id INTEGER NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        ref_hash TEXT NOT NULL,
        detail TEXT NOT NULL,
        found INTEGER NOT NULL,
        import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL);
    INSERT INTO sync_skipped_39(id, connection_id, profile_id, ref_hash, detail, found, import_id)
        SELECT id, connection_id, profile_id, ref_hash, detail, found, import_id FROM sync_skipped;
    DROP TABLE sync_skipped;
    ALTER TABLE sync_skipped_39 RENAME TO sync_skipped;
    CREATE UNIQUE INDEX sync_skipped_ref ON sync_skipped(connection_id, ref_hash);
    CREATE INDEX sync_skipped_profile ON sync_skipped(profile_id);

    INSERT INTO sqlite_sequence(name, seq)
        SELECT column1, 0 FROM (VALUES ('users'), ('profiles'), ('sessions'), ('passkeys'), ('uploads'), ('jobs'),
            ('events'), ('series'), ('edits'), ('labmaps'), ('smart_clients'), ('connections'), ('same_results'),
            ('calendar_feeds'), ('journal'), ('share_links'), ('journal_photos'), ('med_reminders'), ('med_doses'),
            ('sync_skipped'))
        WHERE column1 NOT IN (SELECT name FROM sqlite_sequence);
    UPDATE sqlite_sequence SET seq = MAX(seq, (SELECT COALESCE(MAX(session_id), 0) FROM activity)) WHERE
        name='sessions';
    """,
    # version 40: a lab event's rows each found by its own record (#899). Since #885 the FHIR importer gives a labitem's
    # details "keys", each row's Observation; one saved before has none, so a result its source amended and sent again
    # was never found as the same record (an upload added a copy, a sync left it as it was beside its row updated). The
    # rows and results don't say their Observations, and they're encrypted, so this only marks the work: at start-up
    # sync.backfill_keys gives the labitems a connection's pulls saved, or changed in place, theirs from the
    # connection's store (the records it has read), and clears the mark. An upload's keep none: its files are gone.
    """
    INSERT OR REPLACE INTO meta(k, v) VALUES('labitem_keys', '1');
    """,
    # version 41: a review's lab-name match is its profile's (#927; labmap.py). profile_id: the profile whose review
    # made it, whose imports alone it's applied to, its lookup keyed with it; NULL, an admin's, for every profile. One a
    # review made before is its import's profile's: labmap.scope_old keys its lookup with it at start-up (that needs
    # the pepper) and clears the mark. One whose import is gone (undone, or its profile deleted, after another import
    # used it) has no profile to be: for every profile, but to check (#569) unless #546's rule would make it. One an
    # admin changed is the admin's, for every profile, as it was.
    """
    ALTER TABLE labmaps ADD COLUMN profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE;
    CREATE INDEX labmaps_profile ON labmaps(profile_id);
    UPDATE labmaps SET profile_id = (SELECT profile_id FROM imports WHERE imports.id = labmaps.import_id) WHERE changed IS NULL;
    UPDATE labmaps SET checked = NULL WHERE changed IS NULL AND profile_id IS NULL;
    INSERT OR REPLACE INTO meta(k, v) VALUES('labmaps_scope', '1');
    """,
    # version 42: wrong authenticator codes since the last right one (#928; accounts.code_try). At twofactor.STOP_AFTER
    # the account's sign-in with a code stops until a recovery code is used or an admin resets two-factor sign-in.
    """
    ALTER TABLE users ADD COLUMN totp_fails INTEGER NOT NULL DEFAULT 0;
    """,
    # version 43: an upload put back from an export by server.manage import says so (#944; uploads.orphaned keeps it).
    # Its maker was NULL, and so is the maker of an upload whose account was removed: one sent for a record typed in
    # that was never saved was then kept for good. One from before that no account made and no job read may be either:
    # kept, as it was.
    """
    ALTER TABLE uploads ADD COLUMN put_back INTEGER NOT NULL DEFAULT 0;
    UPDATE uploads SET put_back=1 WHERE created_by IS NULL AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.upload_id=uploads.id);
    """,
    # version 44: records saved before #1022 from a server that stamps meta.source per write keyed by their stable
    # source (#1032). They were keyed "fhir:#<request>|<type>/<id>", so each was added again as a copy when it changed
    # at its source. Their keys are encrypted, so this only marks the work: at start-up records.rekey_stamped keys them
    # as the FHIR importer does now (the corrections and Costs marks that find them too), and clears the mark.
    """
    INSERT OR REPLACE INTO meta(k, v) VALUES('fhir_stamped_keys', '1');
    """,
    # version 45: the copies #1022's bug made, which version 44's keying left sharing one key (#1068): at start-up, once
    # that's done, records.merge_copies makes each one record (the latest kept) and clears the mark. Its own mark, so
    # an install whose version 44 work has already run finds its copies too.
    """
    INSERT OR REPLACE INTO meta(k, v) VALUES('fhir_stamped_copies', '1');
    """,
    # version 46 (#1091): a profile's own medications and its changes to ones from the records (medications.py,
    # "Your own medications"). One row each: an entry of the person's own (its key "own:" and a random word), or the
    # changes to, or the hiding of, a medication of the records, found by its grouping key (medications.med_key), so
    # undoing an import or a later pull keeps it. What it says is in body, encrypted; who made or changed it and when
    # are plain, as the medication log's are.
    """
    CREATE TABLE med_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,                        -- JSON, encrypted: {key, own, name, strength, dose, ..., hidden}
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,                -- the username then
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    CREATE INDEX med_entries_profile ON med_entries(profile_id);
    """,
    # version 47: a medication reminder becomes a line of the medication's schedule (#1094; med_reminders.py): its
    # times, days, from, until and zone are the line's, read with no amount at each time (its body is encrypted, so
    # med_reminders.listing reads one from before as that). One that was on is a line with Remind me on, in the
    # calendar feed as it was; one turned off is a paused line (the owner, on #1090): kept with its times, its doses
    # on no Today and in no count, and in no calendar, until it's resumed, when it reminds as it did.
    """
    ALTER TABLE med_reminders RENAME COLUMN active TO remind;
    ALTER TABLE med_reminders ADD COLUMN paused INTEGER NOT NULL DEFAULT 0;   -- 1: paused, kept to resume
    UPDATE med_reminders SET paused=1, remind=1 WHERE remind=0;
    """,
    # version 48: a profile's changes to its lab-name matches (#1097: labmap.set_matches, remove; Settings → This person
    # → Labs and ranges, and Labs' Same test as…), each with the results it re-filed: series, how many series it charts
    # under another test (0: none, kept for its Undo only, a while); value, encrypted: {"series": {series id: the name
    # it's charted under}, "before": the matches as they were, "after": as it left them}. Applied as the records are
    # put together (records._assemble), never written into them, so its Undo (deleting it) is exact.
    """
    CREATE TABLE labmap_changes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        series INTEGER NOT NULL DEFAULT 0,
        value TEXT NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        at INTEGER NOT NULL);
    CREATE INDEX labmap_changes_profile ON labmap_changes(profile_id, series);
    """,
    # version 49: sign in with OIDC (#1109; oidc.py). An identity at the install's sign-in provider, linked to an
    # account by its person or an admin: the provider's issuer (without a trailing slash), the ID token's sub, the
    # account, when it was linked and last signed in, and the name the provider gave for it (encrypted), to show. One
    # identity is one account's; linked_by: the admin who linked it by its subject (its person is told). A session made
    # by an OIDC sign-in the provider said used more than one factor (idp_mfa) counts as signed in with a second factor
    # (accounts.signed_in_with_two); stepped_at: when its person last confirmed it was them with a code, a passkey or the
    # provider (never a sign-in, never the password), which stands in for the password while Sign in with OIDC only is on.
    """
    CREATE TABLE oidc_identities(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issuer TEXT NOT NULL,
        subject TEXT NOT NULL,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT,
        created INTEGER NOT NULL,
        last_used INTEGER,
        linked_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        UNIQUE(issuer, subject));
    CREATE INDEX oidc_identities_user ON oidc_identities(user_id);
    ALTER TABLE sessions ADD COLUMN idp_mfa INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE sessions ADD COLUMN stepped_at INTEGER;
    """,
    # version 50: spots (#1139; journal_photos.py): one place on the body over time, a mole, a rash or a wound, named
    # by the person ("Left forearm mole"; encrypted) and tagged on the journal photos of it, to look at side by side.
    # A photo is of one spot or none (spot_id); a spot no photo is of any more goes (journal_photos.tidy_spots).
    """
    CREATE TABLE journal_spots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created INTEGER NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL);
    CREATE INDEX journal_spots_profile ON journal_spots(profile_id);
    ALTER TABLE journal_photos ADD COLUMN spot_id INTEGER REFERENCES journal_spots(id) ON DELETE SET NULL;
    """,
    # version 51 (#1127): a profile's plan years, typed in on the Costs tab (costplan.py): each one's start date,
    # deductible and out-of-pocket maximum, and the family amounts when given, in body, encrypted; who typed or changed
    # it and when are plain, as med_entries' are.
    """
    CREATE TABLE cost_plans(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,                        -- JSON, encrypted: {start, deductible, oop, familyDeductible?, familyOop?}
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    CREATE INDEX cost_plans_profile ON cost_plans(profile_id);
    """,
    # version 52 (#1128): a provider's bills, typed in on Costs (costplan.py): each one's date of service, provider,
    # amount billed, due date, the claim it was lined up with by hand (that claim's key, or "" for none yet) and its
    # file (the document's key), in body, encrypted; who typed or changed it and when are plain, as cost_plans'.
    """
    CREATE TABLE cost_bills(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,                        -- JSON, encrypted: {service, provider, amount, due?, claim?, file?}
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    CREATE INDEX cost_bills_profile ON cost_bills(profile_id);
    """,
    # version 53 (#1134): a profile's own family history (family.py), and its hiding of entries of the records'. One row
    # each: an entry of the person's own (its key "own:" and a random word: a relative and their conditions), or the
    # hiding of an entry of the records', found by its source key, so a later pull or the same records imported again
    # never shows it again. What it says is in body, encrypted; who made or changed it and when are plain, as
    # med_entries' are.
    """
    CREATE TABLE family_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,                        -- JSON, encrypted: {key, own, relation, name, conditions, ...} or {key, hidden}
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,                -- the username then
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    CREATE INDEX family_entries_profile ON family_entries(profile_id);
    """,
    # version 54: notifications to the install's own server (#1130; notify.py). notify_topics: each person's own topic
    # there (random, unguessable; encrypted), which only they subscribe to; made again by Make a new topic.
    # notify_optins: a person's opt-in, per profile they can see and per kind ("results": a connected source's pull
    # brought new records; "doses": a dose of a schedule line with Remind me on is due). notify_sent: each
    # notification, made once for each person who wants it (key: what it's for and whose, "results:<import id>:<user>"
    # or "doses:<profile id>:<day>:<HH:MM>:<user>", so a restart never makes it again), then sent in the background to
    # that person's topic and retried: its state, tries and next try. It holds no message: the words are made as it's
    # sent (notify.message), from its kind and the profile's name alone.
    """
    CREATE TABLE notify_topics(
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        topic TEXT NOT NULL,                       -- encrypted
        created INTEGER NOT NULL);
    CREATE TABLE notify_optins(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        created INTEGER NOT NULL,
        PRIMARY KEY(user_id, profile_id, kind));
    CREATE INDEX notify_optins_profile ON notify_optins(profile_id, kind);
    CREATE TABLE notify_sent(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'queued',      -- queued | sent | failed | dropped
        tries INTEGER NOT NULL DEFAULT 0,
        next_at INTEGER NOT NULL,
        created INTEGER NOT NULL,
        sent INTEGER);
    CREATE INDEX notify_sent_due ON notify_sent(state, next_at);
    """,
    # version 55: insurance cards (#1137; insurance.py). A health plan of a profile: body, encrypted, {name, member,
    # group, phones}; each side's photo a sealed file in profiles/<id>/insurance/ under a random name (NULL: none) and
    # its type. Removing the plan removes its photos (insurance.remove, the files once that's committed).
    """
    CREATE TABLE insurance_plans(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,                        -- JSON, encrypted: {name, member, group, phones: [{label, number}]}
        front TEXT, front_type TEXT CHECK(front_type IN ('jpeg', 'png', 'webp')),
        back TEXT, back_type TEXT CHECK(back_type IN ('jpeg', 'png', 'webp')),
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,                -- the username then
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    CREATE INDEX insurance_plans_profile ON insurance_plans(profile_id);
    """,
    # version 56: follow-ups from a visit (#1138; followups.py): the person's own list of things to do, each with an
    # optional date and the record it's about. What it says is in body, encrypted ({text, due, said, from, about, show,
    # done, done_at}); who added or changed it and when are plain, as the journal's are.
    """
    CREATE TABLE followups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    CREATE INDEX followups_profile ON followups(profile_id);
    """,
    # version 57: records requests (#1133; rec_requests.py): each request a person makes for their own records, with
    # the rule its deadline is counted by. What it says is in body, encrypted ({to, how, sent, rule, days, ext_days,
    # extended, status, received, from, until, kinds, format, name, note, show}); import_id: the import of the records
    # it brought, once linked (NULL again when that import is undone); who added or changed it and when are plain.
    """
    CREATE TABLE records_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,
        import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_name TEXT NOT NULL,
        created INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        changed_name TEXT,
        changed INTEGER);
    CREATE INDEX records_requests_profile ON records_requests(profile_id);
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
    upgrade(db)
    db.close()


def upgrade(db):
    """Run the schema steps db hasn't had. With foreign keys off, as SQLite says to make a table again that others
    point at (version 37): dropping the old one would otherwise take their rows with it. Each step is checked before
    it commits: it may leave no row pointing at nothing that wasn't so before."""
    have = db.execute("PRAGMA user_version").fetchone()[0]
    if have >= len(SCHEMA):
        return
    db.execute("PRAGMA foreign_keys=OFF")
    try:
        for v, sql in enumerate(SCHEMA[have:], start=have + 1):
            before = set(map(tuple, db.execute("PRAGMA foreign_key_check")))
            db.executescript("BEGIN;" + sql)
            broken = set(map(tuple, db.execute("PRAGMA foreign_key_check"))) - before
            if broken:
                raise RuntimeError(f"schema version {v} would leave rows pointing at nothing: {sorted(broken)[:5]}")
            db.execute(f"PRAGMA user_version={v}")
            db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.execute("PRAGMA foreign_keys=ON")


_after = {}   # id(connection): what to do once the transaction it's in (transaction()) commits (after_commit)


@contextmanager
def transaction(db):
    """BEGIN IMMEDIATE (take the write lock now, so checks inside see what they will change), then COMMIT, or
    ROLLBACK if anything inside raises. What after_commit was given inside runs once it has committed."""
    db.execute("BEGIN IMMEDIATE")
    _after[id(db)] = later = []
    try:
        try:
            yield db
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
    finally:
        _after.pop(id(db), None)
    for fn in later:
        fn()


def after_commit(db, fn):
    """Run fn (removing a file, say: #591) once what db is changing is saved: at once outside a transaction, after the
    COMMIT inside one transaction() opened, never if it rolls back. Inside a transaction begun some other way, not at
    all (returns False): what fn would remove is left for a sweep that finds it, never removed before it's committed."""
    if not db.in_transaction:
        fn()
        return True
    later = _after.get(id(db))
    if later is None:
        return False
    later.append(fn)
    return True


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
