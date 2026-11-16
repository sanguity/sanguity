# Changelog

What changed in each release of Sanguity, and what to do when upgrading to it. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

**Upgrading in general:** database migrations run when the app starts; there is no separate step. Read the
**Upgrade notes** of every release between yours and the new one, take a backup first
(`python -m server.manage backup`), and see [Updating](README.md#updating) in the README for the commands.

## [Unreleased]

Everything on `main` so far; it becomes the first tagged release.

### Added

**Accounts and security (Phase 1)**
- Accounts, profiles and roles behind a backend (FastAPI): each person signs in with their own account, each
  person whose health is tracked has a profile, and a profile is shared as owner, editor or viewer (#1).
- First-run setup in the browser with a one-time setup code from the app's log; the first account is the admin.
- Two-factor sign-in with an authenticator app and recovery codes (#65), and passkeys (#72). Admins need a second
  factor, and the most powerful admin actions ask for a fresh check (#85).
- Session lifetimes that depend on the second factor, and an optional idle timeout (#83).
- Common and breached passwords are refused (#15); password and recovery-code hashes are peppered (#84).
- Encryption at rest of records, documents and the watch receiver's data, with a key kept apart from the data
  and its backups, and key rotation (#71, #73).
- Backup, verify and restore (`python -m server.manage backup|verify|restore`) (#16).
- An activity log of who opened, shared or changed what (#21).
- Export everything (#66), delete a profile (#67), remove an account (#68), and repair of profiles left without
  an active owner (#69).
- An account page in three tabs (#88).
- A security review against OWASP ASVS (`docs/security-review.md`) with its accepted deviations (#79, #81).
- `docker/dev-stack.sh`: a test copy of any branch beside a real install, also as a TrueNAS custom app (#14).
- Installs on 32-bit ARM, checked weekly in CI (#80), and weekly dependency updates by Dependabot; the update
  command pulls patched base images (#82).

**Importing in the app (Phase 2)**
- Upload records from the browser on the new Import page: files, folders or zips, streamed and encrypted as they
  arrive, with size and zip-bomb limits (#19, #110).
- Imports run in the background with format detection (#111).
- An importer plug-in contract and registry (#104), with importers for C-CDA (#105), generic lab CSV (#106),
  FHIR R4 bundles (#108), Apple Health `export.xml` (#112), insurer claims from UHC, BCBS and a generic EOB CSV
  (#114), CareSpace / OncoEMR (#115) and Epic MyChart JSON (#120).
- A preview before an import is saved, showing what is new and what is already there, matched on stable source
  keys (#118).
- Import history with undo (#117).
- Typing records in by hand on the Import page: lab results (#116), then every kind of record the app imports
  (#124).
- Corrections in the app, kept through later re-imports (#119).
- The source of every record, shown in its details (#131).
- Unknown lab names are matched during import, with LOINC and similar-name suggestions; the choice is remembered
  (#122).

**Releases**
- Tagged releases with this changelog, and a published multi-arch image, `ghcr.io/sanguity/sanguity`, for
  linux/amd64, linux/arm64 and linux/arm/v7 (#63).
- `compose.release.yaml`: an install from the published image, without a git checkout or a local build. With no
  command, the image runs everything in one container (`docker/allinone.py`).
- Install templates for the TrueNAS, Unraid and CasaOS app catalogs in `deploy/` (#64), not yet submitted.
- CI upgrades the previous release's data to the current code on every pull request.

### Changed
- Records are stored in the database instead of a pre-built `data.json` per profile (#103), which the in-app
  import, undo and corrections need. The page shows the same thing as before.
- The default ports are 8730 (web app) and 8731 (phone uploads) instead of 8080 and 8081.
- A crimson drop logo (#107, #109).

### Removed
- The old single shared login (`auth/htpasswd`): sign-in is by account.

### Upgrade notes

From an install made before accounts:
- After updating, `docker compose logs app` shows a setup code; the account you create with it owns your
  existing records. `auth/htpasswd` and `auth/token.conf` are no longer used and can be deleted.
- The ports changed to 8730 and 8731. To keep the old ones, add `WEB_PORT=8080` and `RECEIVER_PORT=8081` to
  `.env`; otherwise update bookmarks, your reverse proxy and the upload address in the phone app, or watch data
  quietly stops arriving.

From an install that keeps records in `data.json` (before #103):
- Nothing to do. On the first start each `DATA_DIR/profiles/<id>/data.json` is loaded into the database
  (schema version 10) and kept beside it as `data-before-v10.json`, which can be deleted once you've checked
  the app. A file that can't be read is left alone with a message in the log, and the app still starts.
- `corrections.json`'s `not_hospital_stays` are moved into the app's corrections once, at the first start.

Moving an install from a git checkout to the published image: see [Upgrading to a release](README.md#upgrading-to-a-release).

[Unreleased]: https://github.com/sanguity/sanguity/commits/main
