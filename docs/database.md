# Database design

Sanguity keeps everything in one SQLite database, `DATA_DIR/sanguity.db`, plus files in the data folder that it
points at (documents, uploads, previews, watch summaries). The schema is built step by step from `SCHEMA` in
[`server/db.py`](../server/db.py): each step is a numbered version (`PRAGMA user_version`), applied once at start-up,
so an older install upgrades itself. This page describes **schema version 15**. When you add a step, update the
diagrams here too.

Two things hold everywhere:

- **Health information is encrypted.** Every column that holds records, document names or secrets is encrypted with
  the data key (`crypto.enc`, AES-256-GCM), marked 🔒 below. Only ids, links between rows, timestamps, states and
  counts are plain. Where a value must be found without decrypting every row, a keyed hash (HMAC) of it is stored
  beside it (`key_hash`, `target`).
- **Everything belongs to a profile, and goes with it.** Deleting a profile removes its records, imports, uploads,
  jobs, edits, memberships and activity by `ON DELETE CASCADE`. Records also belong to the import that brought them
  in, so undoing an import removes exactly its rows.

## Accounts and access

People sign in as **users**. A **profile** is a person whose health is tracked. **Memberships** link the two with a
role (owner, editor or viewer), so one account can look after several profiles and one profile can be shared.

```mermaid
erDiagram
    users ||--o{ memberships : "has roles through"
    profiles ||--o{ memberships : "is shared through"
    users ||--o{ sessions : "signs in with"
    users ||--o{ devices : "is known on"
    users ||--o{ passkeys : "has"
    users ||--o{ recovery_codes : "has"
    users |o--o{ signin_failures : "failed sign-ins for"
    profiles |o--o{ activity : "logs"
    users |o--o{ activity : "did"

    users {
        int id PK
        text username "unique, case-insensitive"
        text pw_hash "Argon2id, peppered"
        int is_admin
        int disabled
        int must_change_pw
        int pw_short "shorter than the one-factor minimum"
        int temp_pw_expires "one-time password's expiry"
        text totp_secret "🔒 authenticator app"
        text totp_pending "🔒 being set up"
        int totp_last_step "no code reuse"
        text webauthn_handle "passkeys' user handle"
        int created
    }
    profiles {
        int id PK
        text name
        int created
        int linked_build "follows the setup build"
        any build_mtime "which build was loaded"
        text watch_file "the watch summary it shows"
        int records_rev "bumped on every change to its records"
    }
    memberships {
        int user_id PK, FK
        int profile_id PK, FK
        text role "owner | editor | viewer"
    }
    sessions {
        int id PK
        text token_hash "SHA-256 of the cookie"
        int user_id FK
        int created
        int last_seen
        int expires
        int verified_at "last proof it was them"
        text ip
        text agent
    }
    devices {
        text token_hash PK "browsers that signed in before"
        int user_id PK, FK
        int created
        int last_seen
    }
    passkeys {
        int id PK
        int user_id FK
        text credential_id "unique"
        blob public_key "public, so plain"
        int sign_count
        text rp_id
        text name
        text transports
        int created
        int last_used
    }
    recovery_codes {
        int user_id PK, FK
        text code_hash PK
        int used_at
    }
    signin_failures {
        int at
        text ip
        int user_id FK "only when the name was a real account"
        text step
    }
    activity {
        int id PK
        int at
        int profile_id FK "NULL: an account event"
        int actor_id FK
        text actor "the username then"
        text action
        text detail "🔒 JSON"
        text item "what a view was of"
        text ip
        int session_id
    }
```

`meta` (`k`, `v`) holds install-wide values: the pepper for password hashes (encrypted), the setup code's hash, and
one-off migration markers.

## Records and imports

Records come in as **imports**: the setup build, an export loaded with `server.manage import`, an upload from the
browser, or results typed in. A browser upload is stored as raw files (**uploads**, **upload_files**), read by a
background **job** that stops at a preview, and saved as one import once confirmed. An import's **events** (visits,
stays, lab draws, documents, claims…), lab **series** and their **results**, and its **documents** all point back to
it. **Edits** (corrections) belong to the profile instead, keyed by a hash of each event's source key, so they
survive re-imports and undo.

```mermaid
erDiagram
    profiles ||--o{ imports : "has"
    imports ||--o{ events : "brought"
    imports ||--o{ series : "brought"
    series ||--o{ results : "holds"
    imports |o--o{ documents : "links"
    profiles ||--o{ uploads : "has"
    uploads ||--o{ upload_files : "holds"
    uploads ||--o{ jobs : "is imported by"
    jobs |o--o| imports : "saved as"
    profiles ||--o{ import_undos : "remembers"
    profiles ||--o{ edits : "has"
    events }o..o{ edits : "matched by key hash"

    profiles {
        int id PK
        int records_rev
    }
    imports {
        int id PK
        int profile_id FK
        int at
        text origin "build | dataset | upload | manual"
        text format "the importer that read it"
        text meta "🔒 JSON: source, built, problems, upcoming visits, payments..."
    }
    events {
        int id PK "the id the page sees"
        int profile_id FK
        int import_id FK
        text body "🔒 JSON: time, kind, title, source key..."
        text detail "🔒 JSON: what the drawer shows"
        text cost "🔒 JSON: its Costs row"
        int cost_seq "order on the Costs tab"
        text key_hash "HMAC of the source key"
    }
    series {
        int id PK
        int profile_id FK
        int import_id FK
        text body "🔒 JSON: name, unit, panel, LOINC"
        text key_hash "HMAC of the source key"
    }
    results {
        int id PK
        int profile_id FK
        int import_id FK
        int series_id FK
        text body "🔒 JSON: time, value, range, flag"
    }
    documents {
        text id PK "random, can't be guessed"
        int profile_id FK
        int import_id FK
        text path "🔒 file it serves"
        text ext
    }
    uploads {
        int id PK
        int profile_id FK
        int created_by FK
        int created
        text state "receiving | ready | discarded"
        int files
        int bytes
        text manifest "🔒 JSON: stored name, path, size"
    }
    upload_files {
        int upload_id PK, FK
        text name PK
        text file "random name on disk"
        text path "🔒 path in the upload"
        int bytes
    }
    jobs {
        int id PK
        int profile_id FK
        int upload_id FK
        int created_by FK
        int confirmed_by FK
        text state "queued | running | preview | done | failed | discarded"
        text stage "detecting | parsing | saving"
        text importer
        text message "🔒"
        text warnings "🔒 JSON"
        text summary "🔒 JSON: counts, duplicates, range"
        text keep "duplicates saved anyway"
        int import_id FK
        int created
        int started
        int finished
    }
    import_undos {
        int id PK
        int profile_id FK
        int import_id "gone, so not a reference"
        int at
        int by_id FK
        text by_name
        text entry "🔒 JSON: what the history showed"
    }
    edits {
        int id PK
        int profile_id FK
        text target "HMAC of the event's key"
        text target_key "🔒 the key"
        text action "hide | rename | date | not_stay | merge"
        text other "merge: the other event's HMAC"
        text other_key "🔒"
        text value "🔒 JSON"
        int created_by FK
        int at
    }
```

## What is encrypted

| Table | Encrypted columns |
|---|---|
| `events` | `body`, `detail`, `cost` |
| `series`, `results` | `body` |
| `imports` | `meta` |
| `documents` | `path` |
| `uploads` | `manifest` |
| `upload_files` | `path` |
| `jobs` | `message`, `warnings`, `summary` |
| `import_undos` | `entry` |
| `edits` | `target_key`, `other_key`, `value` |
| `activity` | `detail` |
| `users` | `totp_secret`, `totp_pending` |
| `meta` | the pepper |

The list the start-up check and key rotation use is `FIELDS` in [`server/atrest.py`](../server/atrest.py); keep the
two in step. Files the rows point at (documents imported from an export, upload files, previews, imported watch
summaries, settings) are encrypted on disk too. Not encrypted: usernames and profile names (needed for sign-in and
sorting), and your original exports in `RECORDS_DIR`, which Sanguity only reads.

## What a deletion removes

| Deleted | Also removed (cascade) | Kept |
|---|---|---|
| A profile | memberships, imports with all their rows, documents, uploads and their files, jobs, edits, undo history, its activity | the original exports |
| An import (undo) | its events, series, results and documents; its upload's raw files unless another import uses them | edits (they belong to the profile) |
| An upload | its files and jobs (a job's saved import stays) | the import it became |
| An account | sessions, devices, passkeys, recovery codes, memberships | activity, uploads and jobs it made (their links become `NULL`) |
