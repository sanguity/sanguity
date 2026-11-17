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
  (#114), CareSpace / OncoEMR (#115) and Epic MyChart JSON (#120), and SMART Health Links and Cards (#127).
- A preview before an import is saved, showing what is new and what is already there, matched on stable source
  keys (#118).
- Import history with undo (#117).
- Typing records in by hand on the Import page: lab results (#116), then every kind of record the app imports
  (#124).
- Corrections in the app, kept through later re-imports (#119).
- The source of every record, shown in its details (#131).
- The same lab result from two sources (a file and a connected source's sync, say) is flagged in the import preview
  and in the result's details, never merged by itself; once the person confirms it, it shows once, with both
  sources, until they undo the link (#96).
- Unknown lab names are matched during import, with LOINC and similar-name suggestions; the choice is remembered
  (#122).
- Outside connections on the Settings page: every host the install may contact (connected sources with their
  token, revocation and registration hosts, scopes and last contact; Health Link fetches; Health Card issuers'
  keys). All outbound requests go through one gateway that refuses unlisted hosts, and a test fails if any code
  contacts one (#97, #59).
- Claims as FHIR: CARIN Blue Button ExplanationOfBenefit (insurers' patient-access APIs, Medicare Blue Button
  exports) becomes claims and pharmacy fills on the Costs tab, and an "Insurer" connected source pulls them on a
  schedule (#92).
- What a connection pulls is the person's choice (kinds of records, from a date), attachments follow the install's
  rules with a size budget and "fetch anyway", and a connection that stops syncing says so on the profile's main
  page (#99, #101, #102).

**Settings and dashboard configuration (Phase 3)**
- A Settings page per profile on a settings registry: every setting with its default, who may change it and
  Reset to defaults; settings.json moved in once, settings exported and encrypted (#33).
- The Overview's key-lab tiles chosen and ordered by drag, keyboard or buttons (#36); tabs shown, hidden and
  reordered per profile (#40); a per-user default theme (#45).
- Milestones are added, changed and removed on the Settings page, one marked primary (#39). Visits counted as
  workouts are a rules list there: plain text by default, a regular expression as an option, checked for patterns
  that could make matching slow (#43).
- Each person's own time zone and date format, set from the browser on first sign-in and changed in Settings: the
  timeline, Health, Costs, the drawer, the import pages, the journal and the visit summary (its PDF too: "today"
  and the date format are the person's) split days at that person's midnight (#42).
- A watch receiver secret per profile, made and replaced on the Settings page with the upload address to give the
  phone app, so each person's phone uploads to their own profile (#46).
- Saved Trends views (#37): named sets of labs ("CBC", "Liver") kept per profile. Owners and editors save what's
  charted as a view from Trends, switch between views there, and make one the default, which Trends starts with
  instead of the labs set in Settings; views are renamed, changed and removed on the Settings page too.
- Rename where a name is shown (#38): a lab from its Trends chart, a heatmap row or a result in the details; a
  result's source tag under its Trends table; and an import (the source of every record) from its Source line. The
  new name shows on every tab for everyone the profile is shared with, the name in the records stays on hover and
  in the details, and "Use the original name" puts it back. Lab and tag names are the Names settings; an import's
  name is its own (encrypted, schema version 25) and shows in the import history too.
- Unit preferences, per person (#41): lab tests in mg/dL or mmol/L, weights in lb or kg, temperatures in °F or °C,
  on Trends, the Overview tiles, the heatmap and the drawer. Only exact conversions are made (a change of prefix, a
  fixed molar mass for glucose, creatinine, BUN, calcium and magnesium, lb to kg by definition); any other test
  stays in its reported unit, and the reported value is always shown beside a converted one.
- Reference ranges of your own for results whose report gives none (#44): set under Settings → Reference ranges by
  lab and unit, optionally by sex and age band (with a birth date for the age on each result's day). They colour
  those results on Trends, the heatmap, the Overview and the visit summary, always labelled "range from settings";
  a range or flag printed on a report is never replaced, and a result in another unit gets none.

**Sharing and new features (Phase 4)**
- Reminders as a calendar feed (#51): a private iCalendar address per person and profile, made on the Settings page,
  that your own calendar app (iPhone, Mac, Google Calendar, Outlook) subscribes to. It lists upcoming appointments
  and, for labs drawn at a steady rhythm, the next expected draw as a tentative all-day event. Minimal details
  ("Appointment") by default; rotate or turn it off any time. Sanguity still sends nothing: the calendar app fetches.
- Out-of-pocket costs without a finance-app export (#55): type in what you paid, with its receipt (kept as a
  document) and whether an HSA or FSA paid it. The Costs tab lists every amount with where its number came from
  (typed in, a finance export, or a claim's patient responsibility), matches payments to claims by amount, date and
  provider (or by hand), counts each amount once, and totals HSA and FSA spending by year.
- A journal of dated notes, symptoms and side effects, with a 0–10 severity and an optional link to one of the
  profile's medications (#49). Entries have their own timeline lane, show as markers on Trends (bigger means more
  severe), can be added from any event's panel, and are listed with filters under Journal. Owners and editors
  write them, viewers read them; they're encrypted, kept when imports are undone, and included in exports.
- A medication timeline (#50): each medication in its own row of the Timeline's Medications lane, as spans from its
  start through each dose change to its stop (or on, where the records give no end), with pharmacy fills marked and,
  when ticked, the same rows under the Trends charts. Its panel lists every listing with its dose, status, prescriber,
  source and RxNorm code, the changes and the fills. It shows what the records say and judges nothing.
- A visit summary to bring to an appointment: one or two printable pages (PDF) with results out of range since
  the last visit (each against its own report's range), trends of the key labs, current medications, upcoming
  appointments and your own questions, kept as a draft per profile (#48).
- Install as a phone app: a web app manifest and home-screen icons, and a service worker that keeps the app's
  pages so it opens offline. Offline records are opt-in per device (Account), read-only, and deleted on sign-out,
  when the sign-in ends, or when turned off (#56). Needs HTTPS (or localhost).
- Installing it is offered in the app: an **Install app** button in the header when the browser can install it,
  and an "Install on this device" card under Account with the button, Safari's Add to Home Screen steps on
  iPhone and iPad, or a link to the install's HTTPS address when the page was opened over plain HTTP (#56).
- Share links for clinicians and relatives (#47): an owner makes a read-only link to part of a profile (chosen
  sections, labs and dates) under Account → Profiles, for someone without an account. It expires after an hour to
  90 days, can be revoked any time, stops if its maker stops being an owner, and every view is logged.
- iPhone sync through Health Auto Export (#148): the watch receiver takes its REST API uploads on the same address
  as Life Dashboard Companion's, with the profile's secret as a bearer header, and folds them into the same daily
  summary with the Apple Health importer's mapping (HRV kept as SDNN); repeated and overlapping uploads count once.
  Setup steps for both phone apps are on the Settings page's watch card. Garmin, Fitbit and Withings come in
  through Health Connect or Apple Health instead of integrations of their own (#52, #53, #54).

**Releases**
- Tagged releases with this changelog, and a published multi-arch image, `ghcr.io/sanguity/sanguity`, for
  linux/amd64, linux/arm64 and linux/arm/v7 (#63).
- `compose.release.yaml`: an install from the published image, without a git checkout or a local build. With no
  command, the image runs everything in one container (`docker/allinone.py`).
- Install templates for the TrueNAS, Unraid and CasaOS app catalogs in `deploy/` (#64), not yet submitted.
- CI upgrades the previous release's data to the current code on every pull request.

### Changed
- A new top bar on every page: the records' tabs, an **Add** menu (import records, connect a source, type records
  in, a journal entry) and your own menu (visit summary, share links, settings, account, light or dark, log out),
  in one row; the build and sync line moved to the page's foot. Settings is now a sidebar of groups (this
  profile's dashboard, labs and ranges, milestones and activity; your display and calendar feed; watch and scale,
  connected sources, outside connections), one group at a time, with **Find a setting** to search them all.
- Records are stored in the database instead of a pre-built `data.json` per profile (#103), which the in-app
  import, undo and corrections need. The page shows the same thing as before.
- The default ports are 8730 (web app) and 8731 (phone uploads) instead of 8080 and 8081.
- A crimson drop logo (#107, #109).
- One period for the records tabs (#171), in the bar above them on every tab with time: All, 5 years down to 30
  days, since each milestone (the primary one has its own button), a calendar year, or dates of your own. Picking
  one on a tab keeps it on the others and on the next visit; Costs groups that period's claims by month or by year.
  A period picked on the old Health tab gives way to this one.
- Trends and Lab heatmap are one **Labs** tab, with a Chart / Heatmap switch, and Health is **Body and activity**
  (#172). Saved views, names, units and ranges are unchanged; links to `#trends` and `#heatmap` open Labs as charts
  or as a heatmap, and a saved tab layout puts Labs where the first of the two was, hidden only if both were.
- An **Admin** page for admins, in your menu, with a sidebar (#167): accounts and requiring two-factor sign-in,
  connected-source apps and their sync defaults, the provider directory, lab name matches, outside connections and
  the install's activity. They left Account, Settings, Connected sources and Find your provider, and their old
  addresses open them there; those pages now say what you can do, or to ask your admin, instead of naming server
  settings and commands. The Watch and scale card no longer shows a stray "null" (#175).
- Settings: each group says under its title whose settings these are (this profile's, for everyone it's shared
  with, or just yours) instead of one intro card that was wrong for Display and the calendar feed. One **Save**
  per section, with **Discard changes**, replaces a Save per setting; a change not saved yet is marked, and so is
  its group in the sidebar. On a phone the page title in the top bar is no longer cut off, and each key lab is
  one row, its moves and Remove in a small menu (#174).
- Add → Journal entry opens the form for a new entry from any tab (from another page, it opens the records page
  with the form), and the journal's list, with its filters and search, has a home on the Timeline tab between the
  timeline and the events (#173).
- A **Sources** page, in your menu, with a sidebar (#166): an **Overview** of every way records reach the profile
  (its portals and health plans, its phone and watch, its latest uploads) with when each last brought something and
  the warning of a source that stopped syncing; **Upload files**, **Type records in** and **Paste a Health Link**
  (the Import page's tabs); **Portals and health plans** (Connected sources), where finding your provider is part of
  connecting one, with each vendor's setup steps in one place, Admin; **Phone and watch** (Settings → Watch and
  scale, now with when the phone last sent something); and the **Import history**. The Add menu's items open their
  sections, and the old addresses (`import.html`, `connect.html`, `providers.html`, `settings.html#watch`) open the
  matching one. The import history says again who imported each import and when.
- A **Sharing** page, in your menu in place of Visit summary and Share links (#168): for the profile picked at
  its top, a sidebar of People (who has access and as what, sharing with an account, changing roles, leaving),
  Share links, Calendar feed, Visit summary, Export everything and Activity, with renaming and deleting the
  profile, and making a new one, set apart under Profile. They left Account's Profiles tab, Settings and the
  visit summary page, and their old addresses open them here. Someone who can only view a profile sees who has
  access, the visit summary and their own calendar feed, and no owner's controls; any member can now see who
  has access to a profile (`GET /api/profiles/{id}/members`), and only owners change it.
- Settings is how things look and Account is you (#169). **Settings** keeps this profile's Dashboard, Labs and
  ranges, and Milestones and activity, and your own Display (theme, dates and time zone, units); someone with no
  profile yet now has their Display settings there too (`GET /api/settings/user/sections`,
  `POST /api/settings/user/reset`). **Account** is a sidebar of two groups: Sign-in and security (password,
  two-factor sign-in, passkeys, where you're signed in) and This device (installing Sanguity, offline records).
  Every old address of what moved still opens its new home, also when followed from within the page.
- **The profile picker is in the top bar** on every page about a profile (#170): the records, Sources, Sharing
  and Settings, each profile by its name and your role (owner, editor or view only), or a plain label when you
  have only one. Sources, Sharing and Settings dropped their own Profile choice; Account and Admin have none.
  Switching it redraws the page for that profile in the same section and switches it on every page; an address
  with `?profile=2` still opens that profile, and now opens it on the records page too.
- Shorter help text on the records tabs, Sources, Sharing and Admin (#176): each card leads with one short line
  saying what it does, and the rest of its explanation is behind a **More about this** toggle, the same on every
  page and still read by screen readers. What you need to decide safely stays in the lead: that undoing an import
  removes what it brought, that anyone holding a share link can open it, that an export holds everything, and the
  "not medical advice" notices.
- One look for each kind of control (#193): choices where exactly one is picked (the period presets, Labs' Chart
  or Heatmap, Costs by month or by year, the events' sort, the journal form's kind, and Settings' theme, units and
  sex for reference ranges) are segmented controls, the chosen one tinted, underlined and bold. Things shown or
  hidden one by one (the Timeline's kinds, Costs' HSA and FSA filters, the journal's kinds and "Only the dates
  shown", and the Labs charts' Procedures and imaging, Medications and Journal) are toggle chips: shown is filled
  with the kind's colour and has a check mark, hidden is only outlined with its key grey, rather than faded. The
  journal's kind filter is now a chip per kind instead of a list. Both read clearly in dark and high-contrast modes,
  with a visible focus ring.
- Keyboard and screen-reader basics on every page (#209): **Skip to content** is the first thing Tab reaches on
  every page with the top bar; the browser tab's title names what's shown ("Labs · Ben · Sanguity", "Settings:
  Display · Sanguity"), and a tab or group opened from a link or a sidebar is said aloud. Saves and errors on
  the records page, Sources, Sharing, Settings, Account and Admin are said through one shared status line,
  errors at once. When the details panel covers the page on a phone, the page behind it can't be reached until
  it's closed. With less motion asked for in the system's settings, transitions and smooth scrolling are off.
  Opening an event from the timeline's list with Enter no longer closes it again straight away.
- **The records tab is in the address** (#208): `#overview`, `#health`, `#timeline`, `#labs`, `#labs-heatmap`
  (Labs as the heatmap) or `#costs`, beside `?profile=2`. Picking a tab, or a tile or button that opens one, adds a
  history entry, so Back and Forward move between tabs; a reload or a copied link opens the same tab. The old
  `#trends` and `#heatmap` still open Labs, an address with no `#` still opens the tab last used, and switching
  profile opens that profile's last tab. The "See them on Labs" and "See them on the timeline" links after an
  import name the profile and tab.
- The phone layout is tested at 375 px on every change (#61): the browser check opens every records tab (with its
  filters open, an event's details and the top bar's menus), every group of Sources, Sharing, Settings, Account and
  Admin, the sign-in page and a share link's page, in light and dark, and fails if the page scrolls sideways or a
  button, link or field is cut off at the screen's edge or overlaps another.
- The Timeline's kinds (#189): each kind's chip has a **⋯** menu with **Show only** that kind and **Show all**, so
  "just labs" is one choice; right-click on a chip, or the keyboard's menu key, opens it too. A **Show all** button
  follows the chips while any kind is hidden. A screen reader hears each chip's kind, count and state ("Visits, 31,
  shown"), and focus stays on the chip or menu used. Which kinds are shown is still kept per profile.
- Labs: the name filter beside its list (#192). **Filter lab names** is at the top of the lab list, with how many
  labs match ("3 of 12"), above what's selected, Clear, Defaults and the saved views, which stay in view while the
  list scrolls; the heatmap has the same filter above its rows. The records bar's search box is the Timeline's
  alone, and words typed in either no longer hide medications under the Labs charts. Procedures and
  imaging, Medications and Journal are grouped as **Show on charts:**, apart from the legend.
- The period has one way to pick it (#191): the preset buttons (All, 5y … 30d and the primary milestone) and one
  **More…** menu with the other milestones, the calendar years and **Custom dates…**, worked by mouse or keyboard
  and never cut off. Only one shows as chosen, a button or More… naming what it picked, and the period is said in
  words beside them ("Last 2 years", "Apr 14, 2025 – today", "2025"). The date boxes show only for custom dates (or after
  dragging a chart to a range of its own), with **Clear dates** to go back; custom dates are kept on a reload.
- **Confirmations in the app's own dialog** (#210), not the browser's pop-up: removing a passkey or an account,
  turning off two-factor sign-in or a calendar feed, revoking a share link, a new watch secret, resetting settings
  and the rest now ask in a window that follows light and dark mode, with a title, Cancel and a button named for
  what it does ("Remove passkey", "Turn off feed"). One that can't be undone is red and starts on Cancel; Escape
  cancels, and focus goes back to the button you pressed. Removing an account that alone owns a profile asks for
  the new owner in the same window. The note after signing in with a recovery code is on the sign-in page, with a
  Continue button, and a Dismiss on Admin that fails says so beside it.
- **Every chart has a table, and out of range isn't shown by colour alone** (#60): **Chart | Table** beside each
  Labs chart, the lab heatmap, each card on Body and activity and both Costs charts shows its values as a table
  instead (the heatmap's **Grid | Table**, a table with a row per lab and a column per draw day); the Timeline's chart says its
  events are listed under it. A Labs or Body and activity chart takes keyboard focus: ← and → step through its
  points with the same readout and tooltip as hovering, said to screen readers, Home and End go to the first and
  last, Escape lets go. An out-of-range result is marked ▲ (above its range) or ▼ (below) on the Labs charts, the
  heatmap's cells, the Overview's sparklines and the visit summary's trends, as well as in red.
- Filters say what they leave out (#190). Whenever a tab's filters narrow what it shows, one line says how ("Showing
  9 of 11 kinds · matching “visit”") and ends in **Reset filters**, which puts that tab's filters back but not the
  period: under the records bar on the Timeline (still shown on a phone with the panel closed, where the button reads
  **Filters (2)**), under the Labs name filter, under each Costs list's filters and in the journal's card. The
  Timeline's counts and the Costs lists' totals then say they're for what's shown. Reset filters replaces the
  Timeline's **Show all** button and the Costs lists' **Clear filters** and **Show everything**; each kind's ⋯ menu
  still has Show all.
- The visit summary's PDF says **High** or **Low** beside a trend's Now value when it's out of range, not by red
  alone, so a black-and-white print shows it; the trends table's columns are rebalanced so it fits (#213).
- A Labs chart's reference band follows each result's own range instead of one range for all its results: when they
  differ (two labs, or a lab that changed its range), the band steps with them and a line under the chart's name says
  so, "Reference range differs between results: 0.7–1.3 and 0.6–1.2 mg/dL", compared after any unit conversion; the
  chart's table gives each row its own, and the Overview's sparklines and the heatmap's row names follow suit (#58).
- **Backups in Admin** (#200): **Admin → Backups** says when the newest backup was made, how big it is and where it
  went, and warns when it's late or the last one failed (on every Admin group, beside Backups in the sidebar and on
  the Admin menu item). **Back up now** and a schedule the app keeps (off, daily or weekly, keeping the newest N of
  its own backup files and nothing else in the folder) run in the background. **Check a backup** restores one into a
  temporary folder beside it, compares it with the row counts its manifest now records, says whether it's good and
  deletes the copy; it never touches the live data (also `python -m server.manage check FILE`). Turning the schedule
  off, or keeping fewer, asks for the fresh check. Restoring stays a command-line step with the app stopped.

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
- Milestones, from `corrections.json` and from the records' `data.json`, are moved into each profile's settings
  once, at the first start (#39). After that `corrections.json` isn't read and can be deleted; `build.py` no
  longer reads it. A `visitWorkouts` rule written before #43 keeps working as the regular expression it was.

From an install with one watch receiver secret (before #46):
- Nothing to do for the phone that's already set up: `auth/watch_secret` keeps working as the first profile's
  secret until that profile makes its own on the Settings page. With your own compose file or install template, give
  the app write access to `WATCH_DIR/keys` (`compose.yaml` and `compose.release.yaml` mount it), or secrets made in
  Settings can't reach the receiver; the TrueNAS template doesn't mount it yet.

Moving an install from a git checkout to the published image: see [Upgrading to a release](README.md#upgrading-to-a-release).

[Unreleased]: https://github.com/sanguity/sanguity/commits/main
