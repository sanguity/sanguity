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
  `--compose FILE` keeps the app definition out of the branch's hands; it fetches only a branch or tag of that
  name and refuses links in the code that point outside it.
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
- Connecting a patient portal or health plan is easier to set up (#165). **Admin → Connected-source apps** walks an
  admin through registering this server's app with each kind of vendor (Epic, Oracle Health, health plans, other
  SMART on FHIR servers) in numbered steps: what to look for on the vendor's developer site, the values to paste
  with Copy buttons, the form, and **Test this connection** as the last step. A source can be marked **Waiting for
  the vendor's review** (before the vendor has even given a client ID); Portals and health plans then says it's
  coming and to upload the portal's export meanwhile, with a link to Upload files, instead of a connect button.
  Upload files says that an iPhone's Apple Health export holds the Health app's Health Records, and the Apple
  Health importer now brings them in with the same import (the FHIR files in `clinical-records/`); the FHIR
  importer now reads allergies, immunizations and procedures too. docs/provider-directory.md has a place for
  systems known to work, from people's reports. Schema version 30.
- **A getting-started guide, from a fresh install to a profile with records** (#199): after setup, and whenever you
  sign in to an empty profile of your own until you finish or skip it, **Getting started** walks you through whose
  records the profile holds (its name), bringing in the first records (upload an export, connect a portal or health
  plan, or type them in, each on its own Sources section, with **Back to the guide**), a phone (optional), then the
  Overview. Every step can be skipped, and the guide stays in your menu. Someone given another person's profile gets
  none of its owner's steps: an editor starts at the first records, a viewer is told its owners and editors set it
  up. Empty places say what would appear there, with one next step: an empty profile (**Get started**), each records
  tab with nothing to show (lab results, a phone, claims and payments; the Timeline's **Show everything**), Sources'
  Overview and Import history, share links and the journal. The Overview no longer says everything is in range when
  there are no lab results, and the records page's footer no longer reads "undefined · built" for a profile of
  imports.
- **Big records stay fast** (#201): performance budgets, written down in
  [docs/performance.md](docs/performance.md) and checked in CI by `tests/test_performance.py` on ten years of
  fictional records (`demo/generate.py --big`: 5,000 lab results, 2,000 claims, 1,000 documents, ten years of watch
  data): the records data's size and time, an import of a large export and of a ten-year Apple Health export, a
  connected source's first pull, and each tab's first draw, on a desktop and on a phone-speed CPU. Body and activity
  draws ten years of watch data in a tenth of a second instead of two (eight on a phone); Costs lists 200 amounts at
  a time under its out-of-pocket card, with **Show 200 more**, like its claims; and a big profile's own export
  imports again (it was "not recognised" once its payments filled the first 64 KB).
- **An anonymised sample of an export, for an importer** (#34): `tools/anonymise.py` copies an export (a file, a
  folder or a zip: C-CDA, FHIR, Apple Health, Epic MyChart, CareSpace, lab and claims CSV, claims JSON) with the
  names of people and organisations, identifiers, addresses, phone numbers and emails made up (the same everywhere
  they appear), every date moved back by one random number of whole weeks, birth dates kept to the year, notes and
  messages replaced and documents swapped for placeholders, and keeps the files, fields, codes, units, values and
  ranges, so the copy still imports. It says what it changed and which kept text to read before sharing, and
  refuses PDFs, unsafe zips and XML entities. [docs/anonymising-exports.md](docs/anonymising-exports.md) is honest
  about what it misses; CONTRIBUTING and the portal-export issue form point to it.
- **The security review covers everything on `main`** (#203): [docs/security-review.md](docs/security-review.md)
  now reviews uploads and imports, connected sources and outside connections, SMART Health Links, settings, the
  journal, costs, the visit summary, calendar feeds, share links, phone uploads, offline records, the Admin page and
  backups against the same ASVS chapters, with a table of what an owner, editor, viewer, admin and share-link holder
  can reach. Fixed on the way: nginx keeps no access log (its request lines named uploaded files, provider searches
  and a connected source's sign-in code); a working share link or calendar feed opens even while wrong tokens from
  the same address are being slowed down (behind a reverse proxy everyone shares one address, so a stranger could
  shut every holder out); and a failed import, pull or request is logged with where it happened but not the
  exception's message, which can quote a file's name or a record. `tests/test_security_refresh.py` lists every
  route with the role it needs and every database column as encrypted or plain on purpose.
- **Accessibility reviewed to WCAG 2.2 AA** (#202): every page against each criterion, recorded in
  [docs/accessibility.md](docs/accessibility.md) with what's still open (a real screen-reader pass). Grey and blue
  text and the blue and red buttons' white text pass 4.5:1 in both themes, and a text field's edge 3:1; the rows
  that opened something by mouse only (the Overview's lists, the Timeline's events, claims, paid amounts, a
  medication's listings) and the Costs column sorts are buttons; focus shows on menu items and date fields and is
  scrolled clear of the sticky bars; the ✎ is a 24 px target; the Overview and Costs fit 320 px. The browser check
  runs axe-core's WCAG A and AA rules on about a hundred pages and states in light and dark, failing on a serious
  finding, and walks every page by keyboard, at 320 px, with wide text spacing and in forced colours.
- **Blood glucose, from a meter or a continuous glucose monitor** (#181): the phone receiver reads glucose from both
  apps (Health Connect's blood glucose through Life Dashboard Companion, Apple Health's through Health Auto Export,
  in mg/dL or mmol/L by the exact conversion of #41) and keeps every reading, so repeated uploads count once. Body and
  activity gets a Blood glucose card and tile: a meter's readings as points, and each day a CGM was worn as its low to
  high range and average, with the hours that had readings (a sensor gap shows) and time below 54, below 70,
  70–180 and above 180 mg/dL, labelled as the standard published ranges, not advice. No five-minute reading reaches
  the page, only the days. Chart | Table and keyboard stepping like the other charts; lab glucose stays a Labs series
  and is drawn alongside only when asked. A reading is a CGM's by its specimen when it says so, otherwise by how often
  its source reads (`server/glucose.py`). The performance budgets' big fixture has three years of meter readings and
  CGM days.
- **Immunizations and allergies from FHIR records and SMART Health Links** (#179). Immunizations are a kind of record
  of their own: the vaccine and its CVX code, the date, the dose number (and the series' doses where given), the
  amount, route, site, lot, maker, who gave it and where, as recorded. They come from FHIR `Immunization` (portals'
  exports, Apple's Health Records), from SMART Health Cards (a vaccination card's doses, which have
  no ids, are keyed by their day and vaccine code so a card imported again is found as a duplicate; a dose with only
  a CVX code is named in plain words for the common vaccines), from a C-CDA's Immunizations section (refused and
  planned ones left out), and typed in (**Sources → Type records in → Immunization**). They have their own lane and
  chip on the Timeline, in a colour of their own (3:1 against the page in light and dark), open in the drawer with
  what the record says, and are listed on the Overview, newest first. Allergies from FHIR `AllergyIntolerance` and
  C-CDA are read into one shape (`common.allergy`): the substance, its reactions, the severity and criticality and
  the status as recorded, and the Overview lists them in an **Allergies and intolerances** card under the problem
  list (no longer in it). The FHIR importer also reads `MedicationDispense` (pharmacy fills, beside their
  prescription on the medication timeline, #50), booked `Appointment`s (upcoming visits) and `ImagingStudy` (an
  imaging event, unless a report in the upload describes it), and `Procedure` stays a procedure. The import preview
  and the import history list immunizations, allergies, other problems and upcoming appointments. Records imported
  from FHIR on a build of `main` between #165 and this change have their immunizations as procedures; undo that import
  under **Sources → Import history** and import the files again to have them as immunizations (no release had them
  as procedures, so there is no migration). Connected sources don't pull these types yet.
- **About this person** (#194): a profile's birth date, the sex labs base reference ranges on (female, male or not
  set, kept apart from gender identity, which Sanguity doesn't ask for) and a dated height history are together under
  Sharing → About this person. Heights are typed in cm or in feet and inches, kept in cm, and shown in your units
  (Settings → Units has a choice for heights now, which also converts a clinic's height chart between cm and inches);
  heights a clinic recorded in the records are shown beside the history and added to it only on request. Each detail
  is optional and encrypted; owners and editors change them, viewers see them, and a share link shows them only when
  its owner ticks "About this person". The birth date and sex your own reference ranges use (#44) moved here from
  Settings → Labs and ranges once, for every profile (schema version 30), and an export from before still loads them.
  The records page has helpers for what's built on them next (age on a day in years and months, the sex for ranges,
  the height in effect on a day; `www/js/person.js`, `server/person.py`).
- **Home blood pressure beside clinic readings** (#180): the watch receiver keeps each blood pressure reading a
  home cuff writes to Health Connect or Apple Health (time, both pressures, the pulse when the cuff gives one), from
  either phone app, a reading sent again or by both apps counted once, and sums each day up (readings, average,
  highest). Body and activity has a blood pressure card: each reading's two pressures as a pair of points, at home
  and at visits told apart (the visits' from the records' systolic and diastolic series), weekly averages over a long
  period, the latest readings listed with their time and source, Chart | Table and the keyboard like the other
  charts; a clinic reading keeps its report's flag, and nothing else is judged. A blood pressure chart on Labs links
  to it. The receiver's store adds a pulse column to its readings as it loads an older one.
- **Body temperature and blood oxygen in full** (#184): the watch receiver keeps each body temperature reading from
  either phone app, in °C whatever unit it came in, and sums each day up (average, highest). Body and activity has a
  small card for each: a thermometer's readings, and the phone's daily blood oxygen as a band from the day's low up
  to its average, each beside the readings taken at visits from the records, told apart, with their list,
  Chart | Table and the keyboard. Temperatures are shown in °F or °C as chosen under Settings → Units (or as the
  records give them), the visits' converted exactly. The temperature and SpO2 charts on Labs link to them.
- **Menstrual cycle tracking, opt-in** (#183): periods, flow, ovulation tests, cervical mucus and spotting from
  Health Connect (Life Dashboard Companion) or Apple Health (Health Auto Export). Off for every profile until one of
  its owners turns it on under Sources → Phone and watch; until then the watch receiver takes these records out of
  each upload before archiving it, so nothing of them is kept, not even in `raw/`. Sexual activity is never kept, on
  or off. On, they're kept in the receiver's sealed store and its own `cycle.json` (never in `daily.json`) and shown
  to owners and editors, not viewers: a Timeline lane of their own (periods as bars, the rest as marks), the drawer,
  and a row under the Labs charts when "Cycle tracking" is ticked under Show on charts. Never in a share link; in
  Export everything and the visit summary only when ticked, each time. "Delete all cycle data", and turning it off,
  delete the receiver's copy and rewrite every archived upload without them; the card says what deleting can't reach
  (backups made before, files already downloaded, uploaded Apple Health exports, the phone). The words describe what
  was recorded; nothing predicts a period or a fertile window.
- **ECG recordings and irregular rhythm notifications from a watch** (#182): a Timeline lane and kind of their own
  ("ECG & rhythm"), from Health Auto Export's ECG and Heart Rate Notifications automations (the receiver keeps each by
  its start, so one sent again counts once) and from an uploaded Apple Health export (its `electrocardiograms/`
  files and export.xml's notifications); the same recording both ways is one event. The drawer shows what the device
  said: its classification word for word, the average heart rate, symptoms, the device, lead and software version,
  and nothing of Sanguity's own. A recording's trace is drawn small with a time and mV scale, loaded only when its
  drawer opens (the samples are kept apart, sealed, and never in the records page's data); it takes focus with a
  text summary, the arrow keys step through it saying the voltage, a table sums it up and **Download as CSV** gives
  every sample. Never in a share link or the visit summary. Health Connect has no ECG type. The name and birth date
  at the top of Apple's ECG files are never kept, and `tools/anonymise.py` replaces them in a sample.
- **The Sleep tile's stages in minutes** (#216): on Body and activity, the line under the average sleep said
  "1 min deep · 2 min REM", the nights' average hours rounded and called minutes; it now gives the minutes, as the
  sleep chart's tooltip and table do ("72 min deep · 105 min REM").
- **Respiratory rate and cardio fitness (VO2 max) on Body and activity** (#226): the watch receiver reads VO2 max from
  both phone apps (Health Connect's Vo2Max through Life Dashboard Companion, Apple Health's through Health Auto
  Export) and the Apple Health importer from an uploaded export, beside the respiratory rate all three already
  brought, the same readings giving the same days whichever way they came. Body and activity has a small card for
  each: the phone's daily respiratory rate beside the rates taken at visits (the records' vitals), told apart, a
  report's flag kept; VO2 max as the watch estimates it, in mL/kg/min, without fitness levels of Sanguity's own.
  Chart | Table, the keyboard and weekly averages over a long period, like the other cards.
- **Heart rate zones on workouts** (#197): the commonly published five zones, from a maximum heart rate typed in About
  this person (a new optional field, for owners and editors) or else the common estimate, 220 minus the age on each
  workout's day, labelled so; nothing without a birth date or a maximum, and the page says how to add one. The watch
  receiver keeps each workout's heart rate from both phone apps (Health Auto Export's minute-by-minute readings, or
  only its average and highest; the heart rate samples Health Connect holds for the session's time) as seconds at each
  bpm, and the app works out the minutes in each zone when the page asks (`server/hrzones.py`), so the readings never
  reach the page; an uploaded Apple Health export brings each workout's average and highest. A workout's details show
  the time in each zone with its heart rate range, or its average and highest against the zones; Body and activity has
  a card of the minutes a week in each zone, with Chart | Table, the keyboard, and the period's workouts listed, each
  opening its details. The receiver's store adds a column for it as it loads an older one, reading its earlier
  workouts' heart rate again once.
- **BMI over time** (#195): with a height in About this person, each weight on Body and activity (the smart scale's
  and the clinic visits') has its BMI, worked out with the height in effect on its day: a chart under the weight's,
  the latest in a tile, and a column in the card's table. The number only; the WHO's categories for adults (under
  18.5, 18.5–24.9, 25–29.9, 30 and over) only when turned on, named as the WHO's and remembered per profile, and never
  for a weight taken under 20, whose BMI also needs a height from the 3 months before it.
- **Children's growth charts** (#196): for a profile with a birth date and a sex used for reference ranges, Body and
  activity (with or without a phone) has a Growth charts card: weight, length or height, BMI from 2 and head
  circumference under 2, each measurement taken before 20 over the published percentile curves for age and sex, with
  its percentile in the readout, the table and a list of the latest. The WHO Child Growth Standards from birth to 2
  years and the CDC growth charts from 2 to 20 (BMI for age from the CDC's 2022 extended charts), as the CDC
  recommends, picked as a pair; the tables are bundled (`www/js/growth-lms.json`, fetched only for such a profile) with
  their sources, versions, licences and checksums in [docs/growth-charts.md](docs/growth-charts.md): the CDC's are
  public domain, and the WHO's come from the WHO's own `anthro` package, which the WHO licenses under GPL-3 (its
  who.int tables are CC BY-NC-SA, which isn't bundled). About this person has an optional due date, for a baby born
  early: before 2 years the charts use the corrected age. A child's BMI (#195) says its percentile for age. Weights
  under 50 lb from C-CDA, Epic MyChart and CareSpace records are no longer left out, and C-CDA brings head
  circumference. Where measurements fall, sourced and labelled; never what that means.
- **Connected sources pull immunizations, allergies, pharmacy fills and appointments** (#228): four new kinds to choose
  per connection (Immunization, AllergyIntolerance, MedicationDispense and booked Appointments), and documents now
  read ImagingStudy too. Their scopes are in the defaults the setup screen and its walk-through give (SMART v1 `.read`,
  or `.rs` where discovery says v2; an insurer's stay claims alone; a discovery that lists its scopes type by type is
  taken at its word). They come in as an upload of the same records would, with the same source keys, so a portal's
  export file and a sync count each once. Turning one off stops it being pulled and can remove what it brought, as for
  the other kinds: an appointment, an allergy or a pharmacy fill is told apart from a planned visit, a problem or a
  claim's fill by the resource it came from. A source set up before says which kinds its scopes leave out; connections
  made before ask to sign in again only when someone turns one of those on. The fake SMART server serves each.
- Uploaded Apple Health exports bring in the same new measures as the phone apps (#223): home blood pressure (each
  correlation, with the cuff's pulse when it's there), body temperature and blood glucose (meter or CGM, decided as
  for Health Auto Export) from `export.xml`, summed up by the receiver's own code, so an upload and the app give the
  same days and a reading both brought is listed once. Heights from the phone are offered on About this person beside
  a clinic's, marked as from the phone, each with "Add to the history"; none is added by itself. Cycle records are
  read only for a profile with cycle tracking on, kept as the import's own (undoing it removes them) and never in the
  watch summary; turning tracking off or deleting deletes them, a waiting preview's too. The import preview counts
  the new kinds (cycle records only there).
- **Backups include the phone and watch data** (#227): each profile's watch receiver database, copied as the receiver
  last saved it (still encrypted), and every upload it archived, so home blood pressure, glucose, ECGs and the rest
  are no longer left to a copy of `WATCH_DIR`. The app reads the receiver's folder read-only for it (`compose.yaml`,
  `compose.release.yaml` and the templates mount it so; only `keys/` stays writable). **Check a backup** opens and
  counts it too, and `server.manage restore` hands it to the receiver, which puts it back when it starts, keeping what
  it had aside. **Admin → Backups** says what's in a backup and what isn't, when the receiver last saved, and how many
  uploads came after that. Installs without a phone are unaffected.
- The new measures in the visit summary, share links and Overview tiles (#225): home blood pressure (since the last
  visit: count, average, highest and the readings), glucose by day (a CGM's time in the standard published ranges,
  labelled as the standard), body temperature, blood oxygen, respiratory rate, VO2 max, immunizations, ECGs (what the
  watch said, never the trace) and BMI with a child's growth percentiles (server/growth.py, the page's own tables) are
  sections of their own in the summary's preview and PDF and on a share link's page, each offered only when the
  profile has some and unticked by default; cycle tracking stays the summary's own (#183) and is never offered for a
  link. Settings → Overview → Home readings puts blood pressure, glucose and temperature tiles after the key labs.
  Export everything now carries each ECG's samples (`watch/ecg/<key>.json`), and loading the export keeps them.
- **Home readings typed in** (#224): Sources → Type records in has a form each for blood pressure (systolic,
  diastolic, pulse), blood glucose (mg/dL or mmol/L, and a meal note only if chosen), temperature (°F or °C) and blood
  oxygen taken at home, and for a height. Values a home device can't read, a unit chosen wrong ("looks like mg/dL"), a
  diastolic above the systolic and a date after today are refused, saying why. The readings join the phone's on Body
  and activity's blood pressure, glucose, temperature and blood oxygen cards, said to be "Typed in" in the lists,
  tooltips and tables; one the phone sent already is left out, and one a visit recorded is shown once. Typed again
  for the same date and time, a reading replaces the one before; undoing the save takes them away. A height goes into
  About this person's history, where it's changed or removed (an undo leaves it, as it leaves every setting).
- **Typed-in glucose and blood oxygen in shares** (#232): the visit summary's and share links' glucose and blood
  oxygen sections list the readings typed in at home beside the phone's: each glucose reading with its time and meal
  note (in mg/dL or mmol/L as chosen), each blood oxygen reading beside the watch's daily average and low; one the phone
  sent too is listed once, and the notes say the readings were sent by a phone app or typed in; the two sections are
  now called "Blood glucose at home" and "Blood oxygen at home", as blood pressure's and temperature's are. Without CGM days the
  glucose table leaves out the time-in-ranges columns. The Overview's glucose tile says when its reading was typed in,
  with the meal note, and Body and activity draws typed-in glucose for a profile without a phone's. Typed-in readings'
  times are listed as typed (the clock's time), no longer moved by the phone's time zone.
- **Vital signs from Epic visit records**: an Epic MyChart export's visit records (C-CDA) now bring the vital
  signs taken at each visit (blood pressure, heart rate, temperature, blood oxygen, respiratory rate, height, BMI
  and head circumference) into the same Vitals series the C-CDA and FHIR imports make, where before only body
  weights came; the same reading repeated in several visit records counts once. CareSpace exports that name their
  vitals by LOINC's long names ("Intravascular systolic Arterial system Pres Pt Qn") now bring their blood pressure
  too. Vitals of the same value taken twice on one day are both kept, from every source (lab results still count
  the same value on the same day once). Re-import an Epic export, or rebuild the setup build, to get them.
- **Targets of your own for home readings** (#234): Sharing → About this person has a "Targets of your own" card where
  owners and editors type in a target for each home measure, as the person's care team gave it (blood pressure,
  systolic and diastolic; glucose meter readings; CGM time in range; body temperature; blood oxygen; weight), each with
  a low end, a high end or both, and optionally who set it and when; each is set, changed and cleared on its own, typed
  in the person's units and kept in one unit (mg/dL, °C, kg), sealed with the profile's settings. Body and activity
  then draws each on its card's chart as a band or line, labelled; marks readings at home outside it ▲ or ▼ with "above
  your target" or "below your target" in its table, list and tooltip (readings taken at visits keep their own report's
  flags); and says how many of the period's readings at home were above or below it. CGM days show the time in the
  person's own range beside the standard published one, each labelled: the receiver's summary now also counts each CGM
  day's readings by whole mg/dL (glucose_dist), which the server turns into the day's shares in that range and never
  sends to the page. The Overview's home tiles say it in words. The visit summary and share links offer "Your targets
  for home readings" when one is entered, unticked. Sanguity never suggests a target; with none entered nothing changes.
- **Install templates on backups** (#218): the TrueNAS, Unraid and CasaOS templates now say Sanguity writes its
  backups to the backups folder itself, on the schedule set under Admin → Backups (or with
  `python -m server.manage backup`), and to copy them off the machine too.
- **A visit summary draft per person** (#219): the questions typed for the visit summary are each person's own
  draft for the profile, so one person's never replaces another's (a viewer's an owner's, say); it goes when their
  access to the profile does. Each profile's shared draft moves once, at the upgrade, to whoever kept it last and,
  unless that was a viewer, to the profile's owners and editors (schema step 31, `summary_drafts`).
- **Picked lab names cleared at sign-out** (#220): signing out, or reaching the sign-in page, takes the lab names
  picked on Labs out of what the browser keeps of each profile's view; the period, tab and kinds stay as they were.
- **HTML records can't move their tab** (#221): an HTML record (a portal message, a Health Link's attachment) opened
  with Open in new tab is sandboxed as it is in the records panel, so a refresh in it can't send the tab elsewhere.
  PDFs and pictures open as before.
- **The watch receiver refuses unsigned uploads at once** (#222): an upload with neither a signature nor the
  profile's secret is refused before any of it is read, and so is a wrong secret; after 10 bad signatures or secrets
  in 15 minutes, an address waits (from 30 seconds, doubling, at most 15 minutes) before its next signed upload is
  read. Health Auto Export with the right secret is never held up; a signing app (Life Dashboard) waits only if
  its own address sent the bad ones.
- **Journal: symptom groups, photos, where on the body, mood** (#161): your own named sets of symptoms ("Migraine
  day": headache, nausea, light sensitivity), made on the journal's card and logged in one go, each symptom its own
  entry with its own severity. A symptom or side effect can say where on the body: tap regions of a front and back
  outline (one keyboard stop each, arrow keys and Space) or tick them in a list. The journal's card shows where
  symptoms have been in the timeline's dates as a body map with each place's count written in it, or as a table; a
  place opens its entries. Photos can be attached to a note, symptom or side effect (JPEG, PNG or WebP, up to six,
  20 MB each by `JOURNAL_PHOTO_MAX_MB`): before they're kept their GPS location, camera details, times and other
  metadata are taken out (the orientation and colour profile stay), and the form says what was taken out. They're
  sealed like documents, shown only to people who can view the profile, deleted with their entry, in Export
  everything (`journal/photos/`) and backups, and in a share link only when "Photos in the journal" is ticked. A
  day's mood and energy (0–10 each) is a fourth kind of entry, in its own Mood & energy lane on the timeline and its
  own row under the Labs charts. Schema step 32 adds the `journal_photos` table.
- Export as FHIR (#205): Sharing → Export everything offers a second format, FHIR R4, a standard bundle other health
  apps can read, with a choice of what to include and a note of what a bundle has no place for (the journal, settings,
  workouts, out-of-pocket payments, the activity log). Labs and vitals as Observations with each result's own range
  and flag, visits and stays as Encounters, upcoming visits as Appointments, conditions, allergies, medications and
  fills, immunizations, procedures, imaging, documents (their files beside the bundle in the zip), messages and claims
  (ExplanationOfBenefit); home readings with the Patient as performer and the phone's device, blood pressure as one
  Observation with two components, CGM days as daily summaries, ECGs with the device's classification and the
  waveform; the birth date on the Patient and the sex used for reference ranges as HL7's sex-parameter-for-clinical-use
  extension, never as gender; heights; cycle records only when ticked. Ids are stable; the bundle is written as it's
  sent. Sanguity's FHIR importer reads it back into the same records (home readings, the phone's measures and ECGs
  into the watch data, conversations, a statement's prescriber, a visit's note, values with their precision kept),
  checked over the demo and every FHIR fixture, and every bundle against HL7's FHIR R4 JSON schema
  ([docs/fhir-export.md](docs/fhir-export.md)).
- **Medication reminders through the calendar feed** (#162): owners and editors set reminders on a current
  medication's details (Timeline → Medications): times of day, which days, from and until. The calendar feed lists each
  time as a repeating event in the time zone it was set in (the person's own, Settings → Dates and times), so 8:00
  stays 8:00 when the clocks change; events say only "Medication" unless the feed is set to full details (then the
  medication's name and its instructions as the records list them). A medication that stops in the records ends its
  reminders that day, and its details say so; each reminder can be turned off or removed, and a feed can leave them
  out ("Medication reminders" on Sharing → Calendar feed). Medications typed in work the same; reminders are kept when
  imports are undone, and encrypted.
- **A medication log** (#235): owners and editors log a dose as taken, taken late or skipped, with its time and a
  note, from the medication's details or from the Overview's "Today's medication doses" (a row for each of the day's
  reminder times when #162's reminders are set, else one per current medication; one click logs it now), and correct
  or remove it. Doses show as marks on the medication's span on the Timeline (● taken, ◆ taken late, ○ skipped), as a
  row under the Labs charts when "Doses logged" is ticked, and counted per week of what was logged ("12 of 14 logged
  doses taken"), never of what was expected. From an iPhone, Apple Health's Medications doses (iOS 26) come through
  Health Auto Export's medications automation, read by the watch receiver; Health Connect has no dose events, so from
  Android they're typed only. Encrypted, the profile's own (kept when imports are undone), in Export everything, and
  in the visit summary and share links only when "Medication log" is ticked.
- **Start from a condition** (#186): Settings → Dashboard has a picker of conditions (type 2 diabetes, chronic kidney
  disease, high blood pressure, heart failure, thyroid disease, high cholesterol, liver disease, anemia and cancer
  during treatment), searchable by name or synonym and grouped by body system. Picking one or more previews the
  key-lab tiles of the labs commonly followed for them, interleaved most important first, and their home readings,
  naming and linking each condition's published guideline; labs with no results in the profile are named, never
  suggested. **Fill these in** adds to or replaces the tiles, and can save a Labs view per condition and tick the
  visit summary's measures at first (a new setting, Settings → Visit summary); each is saved as usual, and Discard
  changes and Reset to defaults undo it. A condition the problem list names is offered, never from an allergy; a
  child's profile leaves adult measures out. The definitions are `condition-presets.json`, checked by
  `tests/test_condition_presets.py`, each awaiting a clinician's review; `docs/condition-presets.md` says how to add one.
- **A record's details load when it's opened** (#217): the records page no longer reads every record's details (a
  result's values, a claim's lines, a message, a letter) with the records. The drawer asks for them when it opens one
  (`GET /api/profiles/{id}/details?ids=`, a lab day's results in one request, the same access as the records and
  never a share link's) and keeps them while the page is open, in the chosen units. The records still bring what
  lists, counts and search need: a lab day's results and what the Overview lists of an immunization. With ten years
  of records the page's data is 2.11 MB instead of 3.51 MB (256 KB instead of 443 KB as sent), and its budget is
  lowered to match. The visit summary, share links and both exports read every record's details on the server, as
  before. The offline copy keeps the details of the records opened on that device, as long as the records stay the
  same; one not opened before says so offline.
- **"What is this test?"** (#163): beside a lab on Labs (its chart's header and the list of labs), the heatmap (its
  table, and a ? beside each name in the grid), the Overview's lab tiles and the drawer's results, a link to a
  plain-language page about the test itself: what it measures and why it's done, never what a result means. The pages
  are a small bundled table, `lab-test-pages.json` (MedlinePlus's lab test pages first, each address checked by
  hand), chosen by the series' LOINC code, else its name; a lab with no page has no link. A link is the page's own
  address and nothing else (no value, date or anything about the person), opens a new tab with no referrer and says
  so; the server contacts nothing, so Outside connections are unchanged. `tests/test_lab_test_pages.py` checks the
  table (https on an allowed host, each lab once, the demo's and the starter conditions' labs covered); CONTRIBUTING.md
  says how to add a page.
- **A fuller list of conditions** (#187): Start from a condition now offers 79, in 11 groups (diabetes, thyroid,
  hormones and metabolism; heart; kidneys; liver and digestion; blood; cancer; joints, lupus and vasculitis;
  infections; brain and mental health; pregnancy and hormone therapy; medicines with regular blood tests), among them
  type 1 diabetes, gestational diabetes, atrial fibrillation, dialysis, kidney transplant, hepatitis B and C, Crohn's,
  sickle cell disease, myeloma, PSA, CEA and CA-125 follow-up, lupus, HIV, pregnancy, and lithium, clozapine,
  warfarin and methotrexate monitoring (what's commonly monitored, never a dose). Each cites its own published source,
  with its labs by LOINC code and synonyms for the search; a child's profile also leaves PSA out. The search ignores
  accents and apostrophes and matches a group's name; with two or more conditions chosen, each previewed tile and
  each lab with no results says which it's from, and each condition's own list is behind "Its list". Thyroid disease
  gains hypothyroidism and hyperthyroidism of their own, and liver disease is now named fatty liver disease (MASLD)
  for its source. `docs/condition-presets.md` lists the conditions left out (osteoporosis, breast cancer follow-up,
  COPD and asthma, long COVID, menopause and others) and why.
- **Chart activity isn't a visit** (#236): encounters a portal lists that weren't appointments (chart reviews and
  result checks, phone calls, care management, faxes, refills, documentation, orders-only encounters) are their own
  Timeline category, Chart activity, and no longer counted as visits on the Overview, the visit summary or share
  links; the drawer says the portal recorded them as encounters. A visit titled by its billing code's description
  ("PR OFFICE/OUTPATIENT ESTABLISHED HIGH MDM 40 MIN") gets a plain name ("Office visit", "Video visit", "Hospital
  visit"...), with the description kept in its details as "Billed as". Appointment reminders are always appointment
  messages, without the message's own date in the title. This happens as the records load, so records imported
  before are fixed without a re-import, and corrections made before still apply.
- **Change category, and similar entries** (#237): an entry's drawer has **Change category**, moving it between
  visits, chart activity, procedures, imaging, reports, messages, letters and documents (not lab results, claims,
  medications or hospital stays); like the other corrections it's kept through re-imports and can be undone. A change
  of category or a rename offers the other entries from the same source with the same title, all ticked, to change
  at once, and one Undo takes them all back. **Keep doing this for new imports** saves a rule, listed under Sources →
  Import history with how many entries it moves, so the next sync or upload from that source arrives sorted; an
  entry moved on its own stays where it was put. Schema version 34 (the corrections table made again to take them).
- **The top bar's menus fit a phone** (#244): on a screen 700 px wide or narrower, Add and your own menu open across
  the screen under the top bar, 8 px in from each side, instead of from their buttons' right edge, which put them
  partly off the screen at 390 px and narrower (Settings and Log out cut off on the records page).
- **The Timeline's row style stays on the Timeline** (#246): rows of buttons on Sources, Sharing, Settings, Account
  and Admin no longer have a line under them, an inset or a hover highlight borrowed from the Timeline's list, and
  what's typed into a field is no longer in its label's semibold.
- **Small display fixes** (#247): the hidden "Skip to content" link no longer casts a shadow at the top-left of
  every page; the Timeline's month headings stick just under the top bar instead of behind it; links are in the
  accent colour everywhere, never the browser's blue or purple; and Find a setting with no match says so, with a
  button to clear the search.
- **No more "null" where a part is left out** (#245): a workout without heart rate showed "nullnull" in its drawer, a
  hospital stay and a medication typed in "null", and so did each form under Sources → Type records in without a hint,
  Admin's connected-source rows and their test, a backup's check, the period menu without other milestones, the
  journal's body map with no places, and on Settings a setting without help text and the saved views once there are
  some. They're left out now.
- **Wording and dates** (#248): "Reading the records…" no longer shows a garbled character, and a test fails on any
  file of the app that isn't UTF-8. The drawer's buttons name the Labs tab by its name ("Show this day in Labs",
  "Labs during this stay"), and sharing with an account that doesn't exist says an admin creates it under Admin →
  Accounts. A lab result's Collected and Resulted dates, a claim's services and the Costs claims table are written in
  the person's date format, not as "2026-11-02".
- **Role and account changes that are hard to take back ask first** (#249): on Sharing → People, making someone an
  owner, taking owner away from someone, and changing your own role (saying what you'd no longer be able to do) ask
  in the app's dialog; another person's editor/viewer change stays instant. On Admin → Accounts, Disable, Make admin,
  Remove admin and Reset password ask first, naming the account and saying what happens, before any fresh check;
  Enable doesn't. Cancel puts things back and sends nothing.
- **Chart marks that aren't colour alone** (#250): the Costs running totals' end labels are in ink, each after a
  short line in its colour (the orange and green text was under 4.5:1); a lab day on the Timeline with a result out
  of range has a red ▲ over it instead of a 2 px red dot; on the Labs charts a procedure is a ● and imaging a ■, no
  longer the ▼ that means below range, with both in the legend while they're shown.
- **New share link opens on the first click** (#306): clicked before the share-links panel had loaded its list,
  it did nothing (the form is built from the sections and labs that come with the list); it waits for them now.
- **Chart labels you can read on a phone** (#312): a milestone's label on the Timeline, the Labs charts and Body and
  activity stays inside the chart at both edges, ending in "…" when there isn't room ("tart · End of treatment" was
  all that showed at 375 px), with the full names and dates on hover and, on the Labs and Body and activity charts,
  in what a screen reader says. On the Labs charts a procedure is named only where no other ● or ■ falls on its name
  ("●●■ph node biopsy"); a mark left unnamed keeps its tooltip and still opens its drawer.
- **One copy of the colours, and the stylesheet in layers** (#251): the colour tokens, light and dark, are in
  `www/css/tokens.css`, which every page loads; sign-in and setup no longer keep copies of their own, whose card edge
  was a shade darker (`--ring` .12 there, .10 in the app). `app.css` is in cascade layers (reset, tokens, base,
  components, pages, utilities), and one rule rings whatever has keyboard focus in the accent, replacing ten that
  repeated it; elements that had the browser's own ring have the accent one now. Nothing else looks different.
- **A check that keeps the styles on the tokens** (#252): `tests/test_css_tokens.py`, run in CI, fails when a
  stylesheet or a page's own styles gain a colour written out instead of a token, or a new px font size, corner radius
  or shadow, or when a script sets one more inline style; today's counts are the most allowed, and it asks for a lower
  number when one goes down.
- **The style check has no way round it** (#307): it reads stylesheets, pages and scripts in subfolders of `www/` too,
  and counts a page's `style="..."` attributes and the colours in them and in its `fill`/`stroke` attributes, a colour
  written in a script's string (`el("svg:rect", {fill: "#f00"})`; not an id or a fragment such as `$("#add")`),
  `"style":`, `setAttribute("style", ...)`, `Object.assign(x.style, ...)` and `x.style[k] = ...`. A number format's
  `{style: "currency"}` no longer counts as a style, so those files' allowances are one lower. A failure gives each
  line and value, and the token a colour matches.
- **The style check's last blind spots** (#321): it counts `x.style = "..."`, a named colour chosen by a ternary or a
  default (`fill: on ? "red" : "blue"`), one in markup written in a string (`'<rect fill="red"/>'`), and the strings
  after `a++ / 2`, which it had taken for a regex; "Issue #252" in a sentence is no longer a colour. The focus-ring
  check reads subfolders and catches `:is(button,input):focus-visible` and `body :focus-visible`. What it still can't
  see is listed in its docstring. The Labs procedure-name check in `tests/browser_check.py` now needs at least one name
  at 1400 px, so it fails if the names are gone. Every count is unchanged.
- **Require two-factor says why right beside it** (#323): its replies (why it stays off, and that it's on or off) appear
  right under the checkbox, not below the account form, where a phone on its side didn't show them.
- **The visit summary preview fits a phone** (#324): its tables' cells wrap on a phone, so a date beside a long test
  name isn't cut at the card's edge.
- **Import history names who typed records in** (#325): it said "command line"; the import now keeps the account that
  typed it in, and one typed in before this shows no name. Schema version 35 (`imports.created_by`).
- **Rows of buttons have room under them again** (#309): since #246, Admin → Backups' "Back up now" sat on the
  Schedule heading and its Save on "Check a backup", and Sharing's summary buttons and activity filters touched what
  followed. Every row of buttons on Sources, Sharing, Settings, Account and Admin is 8 px from what's above and below it.
- **Short screens: headings and menus follow the top bar that scrolls away** (#308): under 500 px tall (a phone on
  its side, a large zoom) the top bar scrolls with the page, but the Timeline's month headings still stuck its height
  down the screen, with rows showing above them, and on a phone Add and your menu opened that far down, under a bar
  that had scrolled up. There the headings (and Settings' sidebar) stick at the top, and the menus open right under
  the bar wherever it is.
- **The page's title is readable on the narrowest phones** (#315): at 320 px the top bar's title had about 23 px
  beside the brand and the menus, so Settings read "S." and Getting started "G. / s.". At 400 px and narrower it's on
  the second row, under the brand, with the profile picker beside it where there is one.
- **Add and your menu stay together in the top bar** (#319): on the records pages between 376 and 451 px wide, Add
  stayed on the first row and your menu dropped onto a second row by itself. The two are one group now, side by side on
  the first row when there's room, else together on the next. Under 500 px tall, where the page scrolls, a phone's menus
  are as tall as their items: at 320×180 (1280×720 zoomed to 400%) each had been a box about one item tall.
- **Tables on a phone say what each value is** (#320): at 640 px and narrower the tables on Account, Admin, Sharing,
  the visit summary's preview and a share link's page hide their column headings, which left values such as "off" and
  "10" (Admin → Accounts) or a device's two dates on their own. Each value now has its column's name above it, set from
  the headings for every such table at once; a row's buttons and empty cells have none, and the tables that keep their
  headings on a phone (a backup's counts, the phone's and watch's measures) are unchanged. A screen reader hears the
  name once: the hidden headings are hidden from it too.
- **Sign-in and setup get the app's focus ring** (#310): Sign in, Sign in with a passkey and Create account had the
  browser's thin black or white ring. The reset and the one focus ring moved from `app.css` to a small
  `www/css/base.css` that every page loads after the tokens, sign-in and setup too, so their buttons and fields are
  ringed in the accent, 2 px out, like the app's; their own copies of the box-sizing reset and of a field's ring are
  gone. `tests/test_css_tokens.py` fails if a stylesheet or a page rings focus again for every element.
- **The last dates written as stored** (#311): Admin → Provider directory's "read" date, the provider finder's, a
  typed-in record's or lab result's check as you type, a connection's "from" date (on Sources and in the activity log)
  and the time offline records were loaded (the offline banner and Account) are in the person's date format, not as
  "2026-11-15".
- **No more "My records's records"** (#313): messages that put "'s" after a profile's name say it another way: "They'll
  stop seeing the records in My records" (Sharing → People → Remove), "Only the owners of …" on Sharing, "This gives you
  access to the records in …" (Admin), "Kept: the records in …" (Account) and "… has records now" (the guide).
- **Confirmations, continued** (#314): Make owner's question starts on Cancel, in red, as Make admin's does. Changing
  your own role is asked only when another owner's account is enabled, as the server counts owners; with only a
  disabled co-owner the server's refusal is said instead, and owners see "(account disabled)" beside such an account
  in People (the members list gives owners whose account is disabled). What you'd lose now includes turning cycle
  tracking on or off and deleting what it kept. Admin → Accounts asks before requiring two-factor sign-in for
  everyone, saying how many accounts have no second factor yet; turning it off stays instant.
- **Import history's dates, and only a typed record's own** (#317): an import's span of dates under Import history is
  in the person's date format ("2 Jan 2024 to 3 Nov 2025"), as in the import preview. The line under a typed-in record
  writes only the record's own dates that way, which the server now sends apart (`summary_parts`); a file's name or a
  title with a date in it ("scan-2026-11-02.pdf") stays as typed.
- **Requiring two-factor sign-in when you have none yourself** (#318): Admin → Accounts asked first and then the
  server refused it. It isn't asked now: it says at once to turn on two-factor sign-in or add a passkey for your own
  account, with a link to Account → Sign-in & security, and the box stays unticked. With a second factor of your own,
  the question names how many other accounts have none.
- **A type scale** (#255): seven sizes (11.5 to 28 px) and four weights, in `tokens.css`, are every size and weight in
  the app's stylesheets and on sign-in and setup, which had 19 sizes and 7 weights. Text that was 13 px is 14, as is
  the line under a card's title that says what to do (12 px grey before); the tiles' values are 28 px. Page and
  section titles and the headline values are in the display serif; values, tables, chart labels and the drawer's facts
  have tabular figures. Each records tab has a heading of its own (hidden: the tab names it) over its sections.
- **Spacing, corners, elevation and motion scales** (#256): spacing on a 4-point scale (4 to 48 px), so what belongs
  together is 12 px apart or less, cards 24 px apart with 16 px inside, and the page's edges 24 px; three corner radii
  and a pill instead of 19 radii; two elevations (a hairline, and menus, tooltips, the drawer and dialogs) instead of
  15 shadows. Tiles inside a card are tinted instead of bordered. What you point at eases into its hover look and a
  menu fades in (120 ms), the drawer slides in 200 ms; none of it moves when the system asks for less motion. On
  Admin → Connected sources the vendor list no longer makes the page wider than a 320 px screen.
- **The app's own fonts** (#253): Inter for the interface and Source Serif 4 for titles (#255), both variable, served from
  `www/fonts/` with their licences (SIL OFL 1.1) instead of whatever `system-ui` is (DejaVu Sans on Linux, where the
  app's 550 and 650 weights fell to 400 or 700). Latin and Latin Extended only, each loaded when a page needs it. Text
  shows at once in Arial or Times New Roman resized to match, so nothing moves when the font arrives. Nothing is
  fetched from anyone else (the CSP is unchanged), the browser keeps the fonts for a year and the service worker keeps
  them for offline use (its copy is `sanguity-shell-v3` now). Code and addresses are in one monospace stack.
  `tests/test_performance.py` has a budget for the static files (`static_bytes`: stylesheets, fonts and icons, 365 KB
  of 390 KB, measured with all of this release's stylesheets).
- **Source Serif is a third of the size, and pages paint sooner on a slow connection** (#328): the serif has its
  weight axis only, without the optical size axis, so its Latin file is 51 KB instead of 122 KB (titles are 15.5 to
  28 px). No page preloads a font any more: on a slow connection Inter's preload shared the bandwidth with the
  stylesheets and held up the first paint by 0.15 to 0.3 s. On a cold load at 1.6 Mbps the sign-in, records and
  Settings pages paint within 40 ms of when they did before the fonts, and the serif arrives 0.4 s sooner on the
  records page and 0.7 s sooner on Settings (docs/performance.md has the numbers).
- **Nothing jumps when the fonts arrive** (#329): the stand-in fonts shown until Inter and Source Serif have loaded are
  sized to them weight by weight, measured on the app's own text, so tiles and cards keep their height when the font
  swaps in (the Overview's tiles grew 18 px, a Settings card 37 px); on a phone, a line that ends within a pixel of
  its box can still wrap differently. Text is laid out at its exact size on Linux too
  (`text-rendering: geometricPrecision`), as on a Mac, which is what makes the sizes hold at 12.5 and 14 px.
- **Icons instead of Unicode symbols** (#257): the ✕, ✓, ✎, ⋯, ⚠, ⇔, ↗, ⠿, carets, arrows, + and the "?" of "What is
  this test?" are Lucide's icons now, drawn in the text's colour from one 2.4 KB SVG sprite (`www/icons/sprite.svg`, 16
  icons, ISC licence beside it; `tools/build_sprite.py` rebuilds it from a list of names), instead of whichever font had
  the character, at uneven sizes and heights. The drawer's ⇔ is two chevrons, apart for wider and together for
  narrower. ▲ and ▼ stay text, the out-of-range marks, as do ◆, ●, ○ and ■ in charts and their legends, and arrows in
  a sentence ("Sources → Upload"). Each icon is hidden from screen readers in a named button or link, or labelled (a
  warning says "Warning:"); they follow the theme and forced colours. The service worker keeps the sprite
  (`sanguity-shell-v4`), and `static_bytes` is 372 KB of 390 KB.
- **Warm, calm colours, light and dark from one set** (#254): warm neutrals (a white card on a warm page in light,
  so cards stand off it; each step lighter in dark), a navy accent for controls, links and the focus ring only (the
  lab data's blue stays apart; an Overview sparkline's last point is in it now, not the accent), red for out of range
  or wrong and green for in range. Each colour token is declared once as a `light-dark()` pair, instead of the dark
  theme written twice; a chosen theme still wins over the system's. Every text colour is 4.5:1 or more and a field's
  edge and the focus ring 3:1 or more on every surface they're on, in both themes: `tools/contrast.py` measures them
  and docs/accessibility.md has the table. The browser's bar and the home-screen icons follow the new page colour.
  The kinds of record keep their colours.
- **Charts no longer draw in the accent** (#334): milestones' dashed rules and labels were navy, the colour of links
  and controls, and so was an ECG trace's cursor. A milestone is a near-black (near-white in dark) neutral now
  (`--milestone`), darker than an event's dotted grey, and every chart's cursor, the Labs and Body and activity
  crosshair and the ECG's, is one grey (`--cursor`), lighter than the trace it crosses. Both are in the contrast table;
  `tokens.css` lists which colours are for controls and which for data.
- **The style check watches spacing, weights and durations** (#333): `tests/test_css_tokens.py` also counts px
  padding, margin and gap outside the spacing tokens (a nudge of 3 px or less is fine), numeric font weights, and
  transition and animation times, in stylesheets, pages and the styles scripts set, so a 7 px margin, a
  `font-weight: 550` or a `transition: .3s` fails it. The scripts' own off-scale spacing (Costs' legends, claims
  toolbar and paid-amount buttons, the Overview's insurance figures, Body and activity's legends and sections) is on
  the scale now, in classes; its hints name the type, radius, elevation, spacing and motion tokens.
- **Tiles' change lines wrap evenly, and their sparklines line up** (#330): in Inter at the new sizes a tile's change
  line wrapped to leave "2026" or "period" alone on a line, and the tiles that wrapped grew, so a row's sparklines
  were at different heights. A date stays on one line, the lines are balanced (on the Overview, Body and activity and
  Costs), and a tile's sparkline sits at its foot, so every sparkline in a row starts at the same height.
- **One h1 per page, and the Overview's cards under their sections** (#331): the brand in the top bar was a heading on
  every page, a second h1 beside the page's own; it's a paragraph now and looks the same. Sources and Admin have a
  title of their own, the group shown, as Settings, Sharing and Account do; the shared page's, the moved pages' and
  the connecting page's titles are h1s. The Overview's cards are h3s: the out-of-range card under Key labs, the
  others under sections a screen reader hears ("Your record", "Recent and upcoming"). Settings' "Preview" heading is
  at the scale's weight.
- **Sign-in and setup fields look like the app's** (#332): on the warm page colour they read as grey, nearly disabled
  boxes on a white card. They have the app's field look now: the raised surface, the small corner and the field edge,
  the same as a field on Account in light and dark.
- **Every table that scrolls can be reached by keyboard** (#335): at 320 px Costs' HSA and FSA by year table scrolled
  sideways but couldn't take focus, so its hidden columns couldn't be reached without a mouse. Every table's scrolling
  box (Costs' four, the heatmap's table, the drawer's results and services billed, a medication's listings and fills)
  takes focus now and is named, as the charts' tables already were.
- **Small text stays crisp** (#338): exact text layout (#329) applies only to tiles and stats, whose rows must not change
  height when the web font arrives; everywhere else the default rendering, which on Linux keeps small regular text sharp.
- **Dates kept together, and only dates** (#339): in the year-month-day format a tile's change line no longer keeps
  "03 since 2026" together; "as of" is kept whole, with the line free to break before its date.
- **Milestones and cursors in forced colours** (#341): against a contrast theme opposite to the app's, they're drawn in
  the system's colours, not faintly in the app's.
- **A table that doesn't scroll is no tab stop** (#340): since #335 every table's box was a tab stop and a region
  landmark whether or not it scrolled, so at 1400 px Costs' HSA and FSA, Prescriptions and Claims were extra stops
  with nothing hidden. A table's box is a named region in the Tab order only while it scrolls, sideways or down, and
  changes as the window or its rows do; the shared page's tables and the share links' list work the same way.
- **Long words and wide figures fit a phone** (#342): opening a document was logged under its stored file's name, a
  32-character hex word that pushed Sharing → Activity out of its card at 375 px and made the page scroll sideways at
  320, as a long IPv6 address did. A document opened is logged under its record's title now ("opened the document
  “PET/CT end of treatment (PDF)”"); older entries keep their file names, and an activity log's entry and address
  wrap, as an account page's table cells do on a phone. The Overview's insurance figures wrap to a second line when
  one is too wide for a third of the card, as "4 772 $US" is in a French browser at 320 px.
- **Dates in more languages kept together** (#345): longer short month names (Finnish "marras", Hungarian "szept.",
  Catalan "ag.") and a point after the day ("18. Aug. 2026").
- **Chart text in forced colours** (#346): event labels, lane labels, medication names and axis text are drawn in the
  system's text colour, like milestones (#341).
- **A table box that doesn't scroll has no name** (#347): it's named only while it scrolls and is a region.
- **One button** (#258): every button is one component in three sizes, 24, 32 and 40 px tall (44 px on a phone or a
  touch screen), and four looks: the default, primary (filled in the accent), danger (filled in red) and quiet. It eases
  into its hover look, darkens a little while pressed, shows the accent's tint and bar when it's a pressed toggle, and
  fades when disabled. The seven copies of a primary button and the per-page sizes (buttons were 25 to 34 px tall)
  are gone; a primary button is filled on every page now, the top bar's and the drawer's buttons are the same
  button, and sign-in's and setup's are the large size.
- **One field, a switch, and the pick-one and on/off controls in one place** (#259): text fields, selects and
  textareas share one look on every page (the raised surface, the field's edge, the small corner, 32 px tall, 44 px on
  a phone) instead of six, with radii of 8 or 9 px and heights from 30 to 41 px; sign-in's and setup's are the large
  size. A setting that's simply on or off is a switch now (a checkbox a screen reader calls a switch, toggled with
  Space): Settings' tabs, Require two-factor sign-in for everyone, Offline records, the calendar feed's lab draws and
  medication reminders, a connection's Fetch automatically and the connection defaults on Admin. A milestone's
  Primary is a radio button, one across the milestones. The time zone is a field you type into to search the zones
  ("los angeles" finds America/Los Angeles); one that isn't on the list says so and isn't saved. Checkboxes and
  radios are in the accent everywhere. The segmented controls and chips (#193) are 32 px tall (Chart | Table 24),
  44 on a phone.
- **One card and one summary tile for every tab** (#260): the Overview's key labs, home readings, Watch & scale and
  insurance figures, Body and activity's figures, the Timeline's counts and Costs' figures are one tile now: its label,
  then the value in the display serif with its unit, then its change line or note, and a sparkline at its foot. Costs'
  and the Timeline's figures had their value above a grey label, and the Overview's insurance figures were a design of
  their own. A tile inside a card is tinted, with no second edge; one on the page has the card's edge. The card and
  the tile are each one rule set; their change lines still wrap evenly with dates kept whole (#330), and their text
  keeps its height when the fonts arrive (#338).
- **Menus and tooltips on the browser's popovers** (#261): the top bar's Add and your menu, the records bar's More…
  periods, the Timeline's kind menus and Settings' row menus are one menu (`SgNav.menu`), a `popover` the browser
  opens over the page and closes on a click elsewhere or on Escape, instead of four copies of that code. Each opens
  under its button by CSS anchor positioning, turning to the other side or upwards where it wouldn't fit, and fades
  in; the keys, focus and phone layouts are as before. Tooltips that carry meaning (an icon button's name, a lab's
  name in the records or cut short, a value's range or date, a claim type's figures) are the app's own instead of a
  `title`: shown on hover and on keyboard focus, above or below what they're about, never over it, kept while the
  pointer is on them, and put away by Escape (WCAG 1.4.13). A `title` stays only for a hint beside a name that says
  it all, on disabled controls and on frames; the drawer's wider-panel button and the shared page's light/dark
  button have names of their own.
- **One dialog, three ways: centred, a side panel and a sheet** (#262): the app's dialog (`ask()`) and the records'
  drawer are one `<dialog>` component (`openDialog` in `util.js`). The drawer is a side panel beside the records as
  before, other records opening in it while it stays open; where it covers the whole page it's a modal dialog, so the
  browser keeps Tab and a screen reader in it instead of the drawer's own code. On a phone it's a bottom sheet with a
  grab handle: swiped down by the handle or its title, it closes (its close button and Escape still do). Escape in a dialog asked
  from the drawer no longer closes the drawer too, and what the app says (the status line) is heard while a dialog
  is open.
- **Toasts, and Undo instead of asking** (#263): what has just happened is said at the foot of the screen, with what
  can be done about it, instead of only in a line that could be out of sight: Settings' saves ("Saved “Key labs”." ·
  Undo, which puts back what each setting was), an import that finished ("Imported 45 lab results in 12 tests." ·
  Open Labs · Undo, Import history's undo at once), records typed in, and removals that can be put back, which no
  longer ask first: a saved Labs view, a target for home readings and a height in About this person. A toast stays
  at least 10 seconds and waits while the pointer is on it or focus is in it; Alt+N goes to it, Escape or its ✕
  puts it away; up to three show at once. It's said once, in a live region named Notifications. What can't be undone
  or touches security (passkeys, sign-in, sharing, cycle data, a dose or reminder removed) still asks first.
- **Undo puts back only what it took away** (#359): a Labs view's Undo saved the whole list as it was when the view
  was deleted, so undoing two deletes in turn deleted the first again, and undoing one after making another view the
  default took that default away. It now puts the one view back into the views as they are, in its place, keeping
  today's default. Settings' Undo puts a setting back only if it still holds what that save wrote (one changed since is
  left as it is, and said to be), and no longer redraws a section with changes not saved: they're kept.
- **An import's Undo asks first** (#360): the Undo on an import's toast, and on records typed in, removed the import at
  one tap, though it can't be reversed (and typed records' form is cleared by then). It's "Undo…" now and asks in
  Import history's words; Keep it leaves the import and the toast. The typed records' Open Labs charts the labs just
  typed, as the line's link does, instead of opening Labs without them.
- **Focus comes back from a toast** (#361): pressing a toast's Undo disabled it while it ran, so focus fell to the page
  (also inside the drawer), and a fourth toast sending away a focused one dropped focus too. Focus that went into a
  toast now goes back where it came from however the toast goes, and stays on the button while it runs. Deleting a
  Labs view leaves focus on the view picker, not nowhere. Alt+N is no longer taken while typing in a field, where
  Option+N on a Mac types ñ, ã or õ.
- **Toasts over nothing you're working on** (#362): toasts drew over an open dialog, so at 568×320 "Delete all cycle
  data?"'s buttons were under one, and over the page's foot, so at 375 px Tab could put focus wholly under one. In a
  dialog, or the drawer, they're now in its flow at its foot, under its buttons and content; over the page, what takes
  focus is scrolled clear of them and the page's foot can scroll up above them. Between 701 and about 1,076 px (and
  with the wider panel) they covered the drawer beside the page; they're in the drawer now. A toast with an Undo stays
  until it's used or put away: on a touch screen nothing could keep one before.
- **A filled button held down keeps its colour** (#350): with Space held on a primary or danger button, or the mouse
  pressed on one and dragged off it, it turned the default button's pale grey under its white label; it keeps its
  accent or red now and only darkens.
- **Tiles keep their figures inside and in line** (#351): on a phone the Overview's insurance figures could run under
  the next tile ("$159,764" at 320 and 375 px); the row wraps instead, as before #260. And in a row of tiles a label
  that wraps (the Timeline's "Imaging, procedures & reports", Costs' "Claim shares with no payment", a key lab's long
  name) no longer pushes its own value below the others: the tiles in a row share their rows, so the values sit on
  one line.
- **A button beside a field is as tall as the field** (#352): Share on Sharing → People, Rename, Clear in About this
  person, the visit summary's Add, a new share link's Copy, and Settings → Dashboard's Add and a lab name's Remove were
  the small 24 px button beside a 32 px field; they're the default size now. The small size stays for buttons in
  dense rows of text.
- **The last controls on the shared components** (#353): Body and activity's "Shade hospital stays", "Show lab glucose
  results alongside" and "Show the WHO's categories for adults", and Costs' "Denied only", are toggle chips instead of
  bare checkboxes; the visit summary's picked labs are action chips; the journal's photos and a typed-in record's file
  are chosen with a button instead of the browser's "Choose Files"; a Labs chart's ✕, the ✎ beside a name and
  Settings' drag handle are the quiet small button, 44 px on a phone like every other; a Labs chart is a card, its
  corner and inset the lab list's on a phone; and a milestone's Primary is a radio of the usual size, named with its
  milestone ("Primary Diagnosis").
- **Icons for the sidebars, the kinds of record and the top bar's menus** (#354): each link of the Settings, Sources,
  Sharing, Account and Admin sidebars, each item of the Add menu and of your menu (Dark mode and Light mode alike),
  and each kind of record have a Lucide icon before their words, hidden from screen readers. A kind's icon is its key
  on the Timeline, in its lane's colour: on its chip (grey while it's hidden) and on each of its rows in the list, in
  place of the coloured dash; one map in `core.js` (`KIND_ICON`) gives every kind its icon. The sprite holds 57 icons,
  10.5 KB, and the service worker keeps the new sprite (`sanguity-shell-v5`).
- **Icons' leftovers** (#355): Settings → Display's theme help names Dark mode and Light mode in your menu, not a "◐
  button" that's gone, and the README says "the pencil" for rename. The Labs legends for the journal, doses and cycle
  tracking keep each mark with its words (● symptom, ◆ taken late, ○ ovulation test) where a phone wraps them. In the
  drawer's results the ? and ✎ beside a name sit on its line, the ? no longer 3 px low, and stay clear of the name
  above when it wraps. A lab in the Labs list with no latest value would have shown "text"; it shows nothing.
- **The last removals asked by hand** (#363): a symptom group is deleted at once, a toast's Undo putting it back
  where it was (the entries logged with it stay either way), as a saved Labs view is. Deleting a journal entry, which
  can't be undone, and Import history's Undo ask in the app's dialog, with a red button and focus on Keep it, instead
  of a confirmation in the page with a blue Delete. The rename form beside a ✎ is a popover: Escape or a click outside
  closes it, focus back on its ✎, as a menu's does.
- **The drawer over a question, a menu and the page** (#364): a phone turned while a question was open over the drawer
  put the drawer, now a modal sheet, on top of it, hiding the question while it waited; the drawer now changes once
  the question is answered. Escape in a menu opened over the drawer closes only the menu. While a dialog or the drawer
  covers the page, the page behind no longer scrolls under the mouse wheel, so what focus goes back to is still on
  screen. The sheet's grab handle shows in forced colours.
- **Escape in the rename form leaves the drawer open** (#366): at 800 px and wider, where the drawer sits beside the
  page, Escape in the form from a ✎ in it (a result's lab, the Source line's import) closed the drawer too, and focus
  was lost. It closes only the form now, focus back on its ✎, as on a phone.
- **Toasts in a short drawer, and a question asked from a toast** (#369): on a phone on its side (568×320), three
  toasts with an Undo filled the drawer, its body 44 px high, the third toast off the screen, and Alt+N scrolled the
  sheet's handle and title away. In the drawer the toasts now take at most a third of its height, and no more than
  leaves its body half of it, scrolling within themselves with the newest in view; the drawer itself no longer
  scrolls. A question (Undo this import?) no longer takes the toasts in under its buttons: they stay where they were,
  behind it, so Tab from its buttons doesn't reach them.
- **A dialog no longer shifts the page sideways** (#370): with scrollbars that take room (Windows, Linux, a Mac set to
  show them), opening a question or the drawer as a sheet hid the page's scrollbar, so everything behind moved 15 px
  and back. The room it took is kept while the dialog is open.
- **Menus in browsers without anchor positioning** (#356): where the browser can't place a menu itself (Firefox and
  Safari before their releases with CSS anchor positioning), a menu near the foot of the screen opened off it, stayed
  where it opened as the page scrolled, and was drawn at the screen's top left for a moment. It now opens over its
  button where there's no room under it, no taller than the room there is (scrolling inside if it must), placed
  before it's drawn, and it and a tooltip follow their button as the page scrolls or the window changes size.
- **The period menu on a phone on its side, and tooltips at the screen's edge** (#357): at 640×360 or 800×450 the
  Timeline's More… menu fitted neither under nor over its button and ran off the right of the screen; a menu is now
  never taller than the larger room beside its button and scrolls inside, so it always opens on the screen.
  Tooltips shifted to fit the screen touched its edge; they keep 8 px from it, as the comment always said.
- **Tooltip text for the keyboard, touch and screen readers** (#358): tips on things that can't take focus (a lab's
  name in the records, its latest date, a nickname's real name, a range) reached only a mouse. A renamed lab's chart
  now says its name in the records beside the unit; the Labs list's checkbox shows its row's tips on focus and is
  described by them; a key lab's tile says a nickname's real name and "against a range from settings" in its name,
  and shows them on focus; the heatmap's table says a row's name in the records and range in its heading and as its
  ✎'s and ?'s description. Titles that said what's on no screen (the Body and activity tab, the milestone preset,
  Clear dates, Connect, Undo link, Same result, Not the same, Hide, a payment's memo) are tips, and a description
  the name already says (the ✎'s "Rename this lab", a test page link's title) is left out.
- **The last titles on no screen** (#371): nine titles still said what no screen shows (a title shows for neither
  the keyboard nor touch, and screen readers don't reliably read it): the brand link's "Back to your records",
  Overview's medication buttons, Save view… and Make default, the drawer's source links, Add a journal note, a journal
  photo's link and Install app. They're tips now, and the edit history's Undo all N says what it undoes in its name
  ("this edit and the N−1 made with it") and its tip. A browser check walks every page, records tab and drawer for a
  title on a control that says what's in neither its text nor its name.
- **A tooltip on something half out of a strip** (#372): at 375 px the milestone preset, half past the right of the
  period's presets, had its tip run off the screen ("Since Ap…"): a tip over an element half out of the screen wasn't
  brought back inside. Tips are placed from the screen's width now, 8 px inside it whatever their element, and an
  element given focus by the keyboard is scrolled wholly into view in its strip first.
- **The static files' budget is 400 KB** (`tests/test_performance.py`, `docs/performance.md`): Redesign 3's icon sprite
  (10.5 KB, 57 icons) and its components took the stylesheets from 140 to 156 KB, 393.6 KB in all, past the 390 KB
  left for them in #328; 6.4 KB of room is left, and more has to be argued for.
- **An Undo that stays puts back only what's still as its change left it** (#367): since #362 an Undo can come long
  after its change. A target's Clear and a height's Remove wrote the old one back over one entered since; they leave
  the new one now, and say so. On Sharing and Settings, which don't reload when you switch profile, an Undo from the
  first profile did nothing (Sharing) or showed its value as saved in the other profile's section (Settings); it now
  changes its own profile only, the page shown keeping its own values, and a toast says what was undone where. A
  symptom group's Undo goes back next to its old neighbours, so two undone in reverse keep their order, and one made
  since under its name is left as it is, said so, as a Labs view's is.
- **Focus after Save and delete** (#368): Settings' Save drew its section again and focus fell to the page, so its
  Undo had nowhere to give it back to; it's on the section's heading now, and back there after the Undo. Alt+N works
  from a list box, such as the view picker where deleting a Labs view leaves focus (it said "Alt+N goes to it" but
  did nothing). After a click on the drawer's text, which focuses the drawer itself, a toast gives focus back to the
  drawer's first control, not the drawer.
- **The ✎ and ? on their name's line on a phone or touch screen** (#373): since #353 the ✎ was 44 px on a phone or touch
  screen, inside the line, so in the drawer's results it sat 10 px below its name, rows grew (45 to 53 px at 375, 33 to
  53 at 1400 with a touch screen) and at 320 px Hemoglobin's ? dropped to a line of its own; on a Labs chart the ✎ sat
  13 px below the lab's name. The ✎ is 24 px again, in its line, and its 44 px target is drawn around the line instead;
  the name's button and the ? beside it keep all of theirs. In the drawer the ✎ and ? follow the name's last word,
  taking it down with them when they don't fit, never on a line of their own.
- **Two more controls on the components, and a key lab's change line kept together** (#374): Settings → Display's
  "Use this device's" (shown when the browser is in another time zone than the one saved) was the small 24 px button
  beside the 32 px zone field; it's the default size, the field's height. Sources → Type records in's ✕ beside a typed
  result and on a typed record's form were their own 28 × 30 px buttons, 30 px tall beside 44 px fields on a phone;
  they're the quiet button, the fields' height and 44 px on a phone. An Overview key lab's change line no longer puts
  the change alone on its first line ("▼ −0.03" over "since Aug 18, 2026"): the change keeps its "since" with it.
- **Settings' Save keeps the page where it is** (#376): since #368, Save moved focus to its section's heading and the
  page scrolled up to it, by mouse too, so what was just saved went off screen (Overview's home readings at 1400 px:
  from 855 px down to the top). Focus still goes to the heading, for the Undo to give it back to, but the page stays
  put. Settings → Display's "Use this device's", which hides itself once pressed, leaves focus on the zone's field,
  and Labs' "Make default" / "Default ✓", drawn again when pressed, on itself; both dropped it to the page.
- **Undoing a setting of your own after switching profile** (#377): Appearance, Dates and Units are yours, the same on
  every profile, but since #367 their Undo pressed on another profile put the server back and left the page as it was:
  the Theme still ticked Dark, the page still dark until a reload, and a toast naming the first profile. It's drawn
  and said on the page shown now, the theme with it, naming no profile. On Sharing → About this person, a change whose
  request was still on its way when you switched profile (a target's Clear, a height's Remove, a Save) was done with
  no word and no Undo; a toast naming its profile says it now, with the Undo, which puts it back on that profile.
- **Type records in: lab results only when it's being typed in** (#381): its section was always on the page, an
  empty row under whatever other record was being typed in. It's one kind among the others now: the page starts with
  its empty row, adding another kind before anything is typed into it puts it away, choosing Lab results brings it
  back, and removing its last row puts it away.
- **Offline, a link that isn't followed says why, and only why** (#378): since #371 the panel's source links, a journal
  photo's link and the "What is this test?" links kept their tip offline ("Open this import in the import history",
  "Open it full size") beside a title saying they need the server: two tooltips that disagreed, on a link that did
  nothing. Their tip and description are the reason now, with no title, and a test page link's name no longer says it
  opens a new tab while it can't.
- **The heatmap's table no longer scrolls on a phone or touch screen, and the weight tile no longer starts a line
  with "·"** (#379): #373's 44 px target around the ✎ reached 4 px past the table's last row, so the table, whose rows
  fit, scrolled by 4 px (with a 15 px scrollbar where scrollbars take room). On a table's last row the target stops
  at the ✎'s foot. The Overview's weight tile read "▼ −4.3 lb since" / "diagnosis" / "· Nov 12, 2026" at 1280 px and
  on a phone; the "·" is kept with the word before it, "diagnosis ·" over the date. So it is in Body and activity's
  Sleep ("70 min deep" over "· 90 min REM") and Blood pressure tiles and Costs' Payments and "Reduced or forgiven".
- **The browser check's fonts check no longer fails now and then** (#380, `tests/browser_check.py`): it read which
  fonts were fetched as soon as the records loaded, before the Overview's tiles had asked for Source Serif on a slow
  runner. It waits for the tiles and for Source Serif (up to 10 s) now: 20 runs in a row pass with the CPU slowed four
  times (the old read failed 16 of them), and it still fails when Source Serif is never asked for.
- **Type records in: what #381 left** (#384): Costs' "Type in a payment" showed the payment's form above an empty lab
  row again, the picker on Lab results; it shows only the payment's form now, the picker on it. A lab row whose date or
  lab had been changed counted as nothing typed and went, without a word, when another kind was added; any change
  keeps it now. After a save the picker reads Lab results again, as on load. And the saved line ("Saved 1 record. See
  them on the timeline") no longer clears itself a moment after Save, and focus goes to it rather than to the page.
- **The Overview's home tiles no longer start a line with "·", and offline menu items say why from the keyboard**
  (#385): with home readings shown as tiles, Blood pressure and Body temperature read "Nov 14, 2026, 8:05 AM" over
  "· at home · Typed in", and Blood glucose had a line of its own starting with "·"; each "·" is kept with the word
  before it, as #379 did for the other tiles, and a time with its AM or PM. Offline, your menu's Sharing, Sources,
  Settings, Account and Getting started, still reached with the arrow keys, gave the reason they do nothing only as a
  title, which the keyboard never shows; it's their tooltip and description now.
- **Type records in after Save: the saved line in sight, a failure's reason kept, and links to where things are**
  (#387): on a phone the line focus goes to after Save was under the new toast; it's scrolled clear of it now. A save
  that failed said why for a moment, then "1 result ready to save." took its place; the reason stays until something's
  changed. And a payment's saved line and toast said "See them on the timeline": payments and claims link to Costs
  now, problems and upcoming appointments to the Overview, the rest to the timeline as before.
- **Type records in keeps what's typed** (#388): on a Sources page whose address names the profile (as Costs' and the
  Overview's links give), Add → Type records in loaded the page again and lost everything typed, without a word. On
  Sources, Add's ways in only change the section now, and leaving the page with something typed in and not saved asks
  first, as Settings does. Arriving from Costs' "Type in a payment" puts focus in "Paid to" rather than on the page.
- **A key lab's "as of" on a line of its own, and a toast's ✕ beside its words** (#389): once a lab dated today was
  typed in, the key lab tiles' "· as of Oct 17, 2026" could start a line with its "·". The "as of" is on a line of its
  own now, under the change, as the fonts drew it anyway, with no "·" to strand, and the tiles keep their height when
  the fonts arrive. A toast with two actions could wrap its ✕ onto a line of its own; its ✕ stays on the first line,
  at the right, with the actions under the words.
- **Log out asks about what isn't saved first** (#391): Log out signed out, then the page's own "Leave site?" asked
  about the lab typed in, the Settings section changed or the upload on its way, too late: Stay left a page that
  couldn't save. Every page's Log out now asks first, in the app's own dialog, and signs out only once you say so.
  And on Sources, Add's items (which only change the section since #388) show it as the sidebar does: from the top
  of the page, with its name said.
- **The static files' budget counts what's sent** (`tests/test_performance.py`, `docs/performance.md`): the stylesheets,
  fonts and icons as nginx sends them, stylesheets and icons compressed, not as they are on disk, where comments
  counted as much as rules. 281.9 KB is sent today (396.3 KB on disk), and the budget is 310 KB, room for Redesigns 4
  to 7. nginx now compresses the SVG icon sprite too (10.5 KB on disk, 3.2 KB sent).
- **Changes update the page in place** (#271): logging a dose, setting a reminder, saving or deleting a journal entry,
  correcting a record (and linking two results as the same) loaded the whole page again, losing where it was
  scrolled and what the panel showed. The records page keeps its records, settings and watch data in a small store of
  its own (`www/js/store.js`), and the API's reply to each of these changes carries what changed (an edit's carries
  the records, since it can change any of them): only the views that show that part are drawn again, the panel
  shows what it showed, and focus stays where it was. Body and activity's "Shade hospital stays" and Costs' "Denied
  only" and type of care keep focus too when they redraw their tab. Switching profile still loads the page again.
- **The same top bar on every page** (#264): Sources, Sharing, Settings, Account, Admin and Getting started showed
  the brand and the page's name, and the records tabs were only on the records page. Every page now has one bar: the
  brand, a **profile menu** (switch profile, each with your role; About this person, Settings, Sources and Sharing
  for that profile; New profile), the records tabs as links (one from Settings opens that tab), **Add** and **your
  menu** (Account, Display, Admin, Getting started, light or dark, Log out). Add has two groups: Today (Log a dose,
  which opens the Overview's "Today's medication doses"; Journal entry; Home reading, a blood pressure's form under
  Type records in) and Records (Upload, Connect, Type in, Health Link). Tab and Enter go from tab to tab instead of
  the arrow keys, and a tab picked opens as a View Transition, at once when less motion is asked for.
- **Tabs that don't fit go under More** (#265): as many tabs as fit, in the profile's order, the rest under **More**,
  which ends with **Edit tabs…** (Settings → Tabs). The tab shown always stays in the bar, taking the last place.
  All five fit at 1,100 px and wider (all six at 1,400 px since the Medications tab, #275); on a phone they're the
  bar at the screen's foot (#268).
- **The drawer: one layout for every record, docked on wide screens** (#270): every kind of record (and a journal
  entry, a cycle record, a medication's history) opens the same way: its kind's icon in its colour, the title, one
  line of what it is and when, and a ⋯ menu with the corrections (Hide, Rename, Change category, Fix date, Merge…; a
  journal entry's Edit and Delete), which no longer look like part of the record. Under them, its three or four key
  facts in boxes (a visit's provider, place and diagnosis; a claim's status, billed, paid and your share), then the
  record, the actions that go somewhere (Show this day in Labs, Medication history, Add a journal note) and where it
  came from. Nothing is said twice: the line under the title no longer repeats the details ("Pharmacy fill · … ·
  Pharmacy fill"), a visit has one Source line (with what the record itself names, "as recorded: …"), a lab day's
  results don't repeat its time, and a value keeps its unit on its line on a phone ("14.4 g/dL"). At 1200 px and
  wider the drawer is docked beside the page, which moves over and is drawn again for its width instead of being
  covered, and focus goes back to what opened it; narrower it covers the page's side as before, and on a phone it's
  the bottom sheet (#262). While a record's details load it shows their shape, and a row of the Timeline's list grows
  into the drawer (a View Transition, where the browser has them and motion isn't reduced). Another record still
  opens in it without closing it.

- **One sidebar for Sources, Sharing, Settings, Account and Admin** (#266): the four copies of it are one component
  (`sidebar.js`). Every page's sidebar has its search, which shows the parts of any group that mention what's typed
  or says that none does, with a button to clear it; its groups under labels; the group's title as the page's
  heading; a card's own title only beside another card (Share links no longer says "Share links" twice); and the
  "not saved" mark. Sources' groups are Connected (portals and health plans, phone and watch), One-off (upload, type
  in, Health Link) and History; Admin's are People, Data safety, Connections and Lab matching. The five pages are up
  to 1280 px wide, not 1120. Every address is the same, and what moved to another page is sent on by `moved.js`
  alone; the service worker keeps the new script (`sanguity-shell-v6`).
- **About this person and Targets are on Settings** (#267), under This profile, beside the settings they feed (ranges,
  BMI, growth charts, heart rate zones), with the height history (`about.js`), for the same people as before: owners
  and editors change them, viewers see them. Milestones have a group of their own, and visits counted as workouts
  are Workouts.
  The old addresses (`sharing.html#about`, `#targets`) open them there, with the profile, and every link to them
  (Body and activity's heart rate zones and BMI, a height typed in, the ranges' note) goes there.
- **A command palette** (#273): only the Timeline had a search box, and each sidebar's search found things on its
  own page. **Ctrl+K** (⌘K on a Mac), **/** when you're not typing in a field, or the top bar's new **Search** opens
  one box on every page that finds actions (what Add offers you, New share link for a profile's owners, light or
  dark), the records tabs, every page's sidebar groups and the parts worth going to by name ("password" finds
  Account's Password, "units" Display's Units), lab names (charted on Labs) and events (the Timeline's index, opened
  in the drawer, or the Timeline searched for them). Off the records page the profile's labs and events are read
  from the API the first time it opens there. The arrow keys move through what it finds and Enter opens it; a
  screen reader hears it as a combobox and its list. "/" can be turned off at its foot. The pages' groups are one
  list now (`groups.js`), which the sidebars and the palette share; the service worker keeps the new scripts
  (`sanguity-shell-v8`).
- **Phones: the tabs at the foot of the screen, and a top bar of one row** (#268): at 390 px the top bar took three
  rows and, with the period, over a quarter of the screen before anything to read, and the tabs' row scrolled
  sideways, cut to "ody and activity". On a phone the top bar is one row now: the brand, the profile menu, then Add and
  your menu as icons (their names still said). The records tabs are a bar fixed at the foot of the screen, clear of
  the home indicator: each tab an icon over a short name ("Body"), 44 px or more each way, in five places (four and
  More once more than five tabs are shown, the one open always among the four). The page, the toasts, Settings' Save
  bar and the heatmap's month axis end above it, and what takes focus is scrolled clear of it. Under 500 px tall the
  tabs are a row in the top bar again, which scrolls away with the page as before.
- **Phones: the period, the filters and the menus as sheets** (#269): on a phone the period is one pill naming it
  ("Last 2 years ▾") instead of seven presets scrolling sideways and More…; it opens a sheet from the foot of the
  screen with every period (the presets, each milestone, the calendar years) and the date boxes for custom dates. On
  the Timeline, Filters opens a sheet with the search, a checkbox for each kind of record with its count, Reset
  filters and Done, instead of 11 chips on nine rows. The top bar's menus (the profile's, Add, yours, More) are sheets
  there too. Each is the sheet of #262: worked by touch or keyboard, closed with Escape, its ✕ or a swipe down.
- **One save model for Settings, Sharing, Account and Admin** (#272): there were five ways a change was saved. Now a
  simple preference (the theme, units, the time zone and date format, a tick such as a home reading's tile or a
  measure the visit summary ticks, Admin's sync switches, the calendar feed's options, offline records) is saved as
  it's changed, a toast saying so with an **Undo**; the theme shows as it's picked, this browser's own choice giving
  way to it. Lists and values (key labs, names, ranges, milestones, workout rules, tabs, About this person's details
  and every target, the visit summary's questions, Admin's sync numbers) are marked "not saved" and wait for **one bar
  per group** at the foot of the screen, "3 changes · Discard · Save", instead of a Save per section (five on
  Dashboard) or per target (six on About this person); the questions are no longer saved as you type. Its Undo, and
  focus on the section's heading without scrolling, are as Save's were (#359, #367, #368, #376, #377). Reset to
  defaults (now with an Undo, asking first only when it would lose changes not saved) and the "your own" or "set for
  this profile" mark show only when something isn't the default. A profile renamed and another person's editor or
  viewer role changed have an Undo too; what can't be undone or touches security still asks first. Leaving the page,
  logging out or picking another profile with changes not saved still asks, on Sharing and Admin too.
- **Costs: what you paid first, then the claims** (#278): fourteen figures came before any chart, and the out-of-pocket
  part interrupted the claims halfway. A **Claims | What you paid** switch now shows one at a time, each with three
  figures first (your share, paid by insurance and billed; or the total out of pocket, the payments and the claim
  shares with no payment) and the rest under **More figures**, so at 1,400 px the claims' chart is on the first screen
  (on a phone 700 px down, not 3,000). Money is right-aligned in tabular figures, a claim's status shows only when it
  isn't "processed", and under 600 px each claim is a stacked row (its name and your share, then when, who and what,
  then billed and insurance paid), so the claims no longer scroll sideways and hide your share.
- **The command palette opens the best match, finds everyday words, and stays within reach offline** (#403): Enter
  opened the first group's first match and a word was found inside another, so "hem" opened Dark mode ("theme"),
  "alt" Paste a Health Link and "cre" New profile. Now one ranking runs across every group (a title starting with
  what's typed, then each word starting a word of the title, then what else it's known by), always from the start of
  a word, and Enter opens the best: Hemoglobin, ALT, Creatinine. "dob", "date of birth", "child", "doctor" (Share
  links and the Visit summary), "medication" and the current medications' names (Log a dose), and "kg" (Units) find
  their places. Offline, pages and settings and what opens them aren't listed, as the menus don't follow them, and
  it says so. Opened from a menu, Escape puts focus back on the menu's button. A sidebar's search that finds nothing
  offers **Search everywhere for “…”**, the palette with the words; Settings' box is "Find in Settings", as the others are.
- **The command palette on a phone, offline and while the records load** (#412): on a phone, where Add's list is in
  its sheet, the palette found none of Add's actions ("medication" found nothing, "upload" only the Sources page); it
  reads Add's list wherever it is now. Offline on the records page it offered New profile, which opened the guide
  from the browser's copy, and Log a dose, which Add shows disabled; neither is listed now. "child" finds New profile
  again. Off the records page, Enter pressed before the records had arrived opened the best of what was there ("cre"
  opened New profile); it waits for them now and opens the best match then (Creatinine), unless an arrow key picked one.
- **A preference on its way to the server isn't "not saved"** (#407): while a unit, the theme or a tick was being
  saved, the bar showed "1 change · Discard · Save", the setting "not saved" and the sidebar its dot, and leaving
  asked; Discard then showed the old value though the new one was saved. Now one on its way counts nowhere, and the
  bar's Discard of other changes leaves it as picked; only one the server refuses waits in the bar, saying why
  (Settings and Admin's sync defaults).
- **Units changed in Display reach About this person and Targets at once** (#408): heights and targets stayed in the
  old units until a reload. Saving or undoing Units draws them again, keeping what's typed there (a target typed in
  °C becomes °F with the unit).
- **The account pages' leftovers** (#409): Admin → Backups' schedule had a Save of its own; how often is now saved as
  it's picked, with an Undo, and how many to keep waits in the bar, marked "not saved", with the sidebar's dot and the
  question before leaving; turning backups off or keeping fewer than would stay is still asked first. On Sharing,
  changing a role or renaming the profile, and their Undo, leave focus where it was instead of on the page. Sources →
  Phone and watch no longer says its title twice (its upload card is "Upload from the phone"). The sidebar's "not
  saved" dot shows in forced colours. A group of one card (Milestones, Workouts) has its Reset to defaults beside the
  group's title, and Workouts' card is named Workouts.
- **One theme** (#410): your menu's Dark mode or Light mode changed only this browser, while Settings → Display kept
  the account's theme and still showed "System". Now it saves the account's theme, as Display does, with an Undo, and
  Display shows it; the command palette offers one theme entry. (Someone who must change their password first, and
  can't save settings yet, still picks one for that browser.)
- **The drawer keeps focus and its place when the page behind it is drawn again** (#404): closing it after a
  correction saved, a dose logged or removed, a journal entry saved or a resize put focus on the page itself, since
  the row or button that opened it had been drawn again. What opened it is found again after every redraw (a
  Timeline or Overview row by its record, so a rename doesn't lose it), and Escape or ✕ takes focus back there. A
  dose logged beside a medication's drawer, or a correction or "Same result" deep in a lab day, drew the drawer again
  at its top with its log folded; it's drawn again in place now, its scroll, what's open and "Show all" as they were.
- **The offline copy follows the changes made on the page** (#405): with Offline records on, a dose, a journal entry, a
  reminder or a correction made since the page loaded wasn't in the copy (reloaded offline, it showed the records as
  they were loaded), and since the server's records had moved on, nothing opened after the change was kept for
  offline until the next full load. Each change's reply now says the records' revision after it (`X-Records-Rev`),
  and the copy takes the change from the same reply the page puts in place, so it stays as the page is and keeps
  collecting what's opened. Costs' out-of-pocket mark loaded all the records again for its part and drew Costs itself,
  emptying the copy's details; its reply carries the out-of-pocket part now, put in place through the store.
- **The drawer says each fact once, by meaning too** (#406): a hospital stay said "Hospital stay" twice in its head
  and its hospital again under it; a medication listing's "Listed since", a fill's "Filled", a vaccine's "Given" and
  a result's "Collected" repeated the head's day in another format; a medication's history had its own list, with
  "Started" and "Still listed" saying what its head said; and an edited record said "Edited" twice. The head says the
  kind once (a stay's title is its hospital, the line under it "Hospital stay" and its dates), a fact that's a day the
  head names or that starts with the place its title is says only what's left ("Where: Oncology unit", "Admitted:
  9:40 PM"), a medication's history has key facts in boxes like every record, and "Edited" is said with the edits.
- **The offline copy follows a source's new name, and the settings saved on the records page** (#416): renaming a
  source from the drawer's Source line left the copy with the old name and the old revision of the records, so
  nothing opened after it was kept for offline (a lab day opened then said offline that its details weren't kept).
  Its reply now says the records' revision after it, and the copy takes the new name. A lab or a source tag renamed,
  a Trends view saved and the journal's groups changed on the records page reach the copy's settings too, where
  offline showed them as they were when the page loaded.
- **The drawer shows what was opened, and focus comes back to something in sight** (#417): with a medication listing
  or fill opened from its history, a dose logged from the Overview turned the drawer back into the medication's
  history, since a listing has the medication's name; the history is drawn again now only when it's what the drawer
  shows. Closing the drawer lost focus to the page after a record opened from the command palette by Ctrl+K, after
  a phone's Add → Journal entry, and after Hide took its Timeline row away. Now focus goes back to what had
  it before the palette opened (found again once the Timeline is drawn), to Add (not the item in its closed sheet),
  or, with that gone, to the record's row, the row now where it was, or the list's heading. Deleting a profile in
  Settings puts focus on the page's heading.
- **Closing the drawer leaves the page where it was** (#421): a record opened by a click on a Timeline chart's mark
  (which can't take focus), then closed, scrolled the page from the top to that record's row far down the list
  (13,902 px at 1100 px wide), and at 1400 px left focus on a row out of sight, since the page was laid out again
  as the drawer undocked. Focus now goes back once the drawer has closed and undocked, without scrolling: to what
  opened it, or else only to something in sight (the record's row, the row now where it was, a heading such as the
  chart's, or a control in the view).
- **The drawer says a count, a supply and a start time once** (#418): a conversation said "2 messages" above its fact
  "Messages 2", an imported pharmacy fill "30-day supply" above "Days' supply 30", and an irregular rhythm
  notification "Period it covers: Nov 3, 2026, 2:00 AM – Nov 3, 2026, 4:10 AM" under a head that says when it began.
  What the line under a record's title says that a key fact says in other words is left out, and the period says
  only its end ("to 4:10 AM", with its day when it's another).
- **The top bar's links open the profile shown** (#397): with a browser tab for each child, Add's links (Log a dose,
  Upload files…), Display and Getting started opened whichever profile had been picked last in any tab, not the one
  shown. Every link in the top bar to another page now carries the profile shown (`?profile=`), as the tabs, the
  profile menu's pages and the command palette's links did.
- **A long profile name stays in its place** (#396): on a phone a name of more than about 13 characters ran over
  Search and Add (a tap on Search opened the profile menu), and on a wide screen it was cut at 16 characters with room
  to spare, shown whole only inside its menu. It's cut short inside its own place now, its ▾ kept, taking the room the
  bar has; where it's cut its whole name is its tip, and still its button's name.
- **More is always whole** (#395): between 701 and 1,330 px wide the tabs' row could shrink and scroll, cutting More
  through or hiding it (on an iPad held upright, or a wide screen with the drawer docked), so the tabs under it
  couldn't be reached. The tabs never shrink now: the profile's name is cut short first, and where not even one tab
  fits beside More, it goes under More too and More takes its name ("Labs ▾"). The tabs are a little narrower, so all
  five still fit at 1,100 px. With the drawer docked, the page is laid out for the room it has: up to 1,500 px wide,
  Labs' list and charts, the Overview's columns and the Timeline's rows are as on a screen 900 px wide.
- **The top bar's small things** (#400): Search is the size of Add and your menu beside it (44 px on a phone or a
  touch screen, where it was 32 or 24 px wide); your menu's name starts with what it shows ("demo, your menu", so
  speech input can say it); and a link from a menu to a part of the page you're on (About this person, on Settings)
  puts focus on its heading, as the command palette's links do, instead of leaving it nowhere.
- **Edit tabs… always in reach** (#398): it was only under More, which isn't there when every tab fits (on a wide
  screen, and on phones). It's in the profile menu too now; it lands with focus on Settings → Tabs' heading (one
  heading: a section of one setting with the same name, as Tabs, Units and Milestones are, no longer says it twice),
  the command palette finds it by "edit tabs", and a new order or a tab hidden there shows in the top bar once it's
  saved, not after a reload. Its help no longer mentions the arrow keys, which #264 replaced.
- **The top bar follows the profile you switch to** (#415): switching profile on Sources, Sharing or Account kept the
  last profile's tabs (a tab it hid stayed out of reach); the new profile's are shown now, on every page. Someone who
  can only view a profile is no longer offered Edit tabs…, which led to switches they couldn't turn. A page opened
  without a profile in its address (after signing in, `/`) now says the one it shows (`?profile=1`), so reloading it
  after another browser tab switched profile keeps it. In forced colours the tab shown, and More when it names the
  tab shown ("Labs ▾"), are marked in the system's highlight; neither was.
- **One icon per thing** (#399): the same item had different icons in different places (Display a gear in your
  menu and a sun in Settings; Body and activity a heart on a phone and a pulse in the palette), and one icon stood for
  several things (the pulse was a home reading, Workouts and Body and activity). Each place now has one icon wherever
  it's shown (the top bar and its menus, a phone's tab bar, the sidebars, the command palette), from one list
  (`groups.js`, `sgIcon()`), and no icon is two places': Display is a screen, Account a key, Workouts a dumbbell, a
  home reading a gauge, New profile a person with a plus, and the groups that shared an icon have their own (16 icons
  added to the sprite).
- **Add follows your role on the profile shown** (#401): Add (and the command palette's actions, which copy it)
  offered every way to add to someone who could only view the profile, on every page but the records page, and there
  to anyone who could add to some other profile; Upload files then said twice that they could only view, and Log a
  dose that there was no current medication when there were. Now its items are for the owners and editors of the
  profile shown, on every page and as you switch profile, and none at all leaves Add out. Log a dose is offered on
  the records page only with a current medication, and from another page says why when there's none to log or the
  profile is one you view. On a profile with no records yet, Log a dose and Journal entry from another page did
  nothing; the first says why and the second opens its form, the entry kept for when the records arrive. Sources
  lists only its Overview and Import history to someone who can only view the profile (as Sharing leaves out its
  owners' groups), and an address naming another, the old import page's, opens the Overview.
- **A profile's name and deleting it are in Settings, and there's one New profile** (#402): renaming and deleting a
  profile were still under Sharing → Profiles after About this person moved to Settings (#267), its card titled "This
  profile", and someone who could only view a profile got a Profile group holding only New profile, a second way to
  make one beside the profile menu's. Now **Settings → This profile → Rename or delete** has them, for the profile's
  owners only, with every question deleting asked before: it's behind "Delete profile…", offers Export everything
  first, and goes ahead only once the profile's name is typed exactly, which the server checks again. New profile is
  the profile menu's alone (the command palette's opens it). Sharing's groups lose their "Sharing" label, which only
  repeated the page's title. The visit summary's **Measures ticked at first** moved from Settings → Dashboard to
  Sharing → Visit summary, beside the summary they tick (owners and editors set them, each tick saved as it's made with
  an Undo; Start from a condition still fills them in), and **Names** (lab nicknames) from Dashboard to Labs and
  ranges. The old addresses (`sharing.html#profile`, `#newProfile`, `settings.html#sec-summary`) land in the new
  places; the service worker keeps the new script (`sanguity-shell-v9`).
- **The account pages' last loose ends** (#419): undoing a tick under Sharing → Visit summary → Measures ticked at
  first left focus on the page, since the boxes were drawn again; it goes back to the tick now. Units is one setting
  for its four kinds, so a second unit picked (lb, then ft/in) waits for the first's answer, and leaving the page
  before it came lost it; it's sent as the page is left now (each such save goes with `keepalive`). Someone who isn't
  the profile's owner following `settings.html#profile` (or Sharing's old address for it) landed on Dashboard with
  nothing said; they're told only a profile's owners can rename or delete it. Rename or delete's name field shows its
  label, "Profile name". docs/database.md no longer says About this person is changed on Sharing.
- **The other pages offline** (#413): offline, only the records page knew it. Settings, Sharing, Sources, Account,
  Admin and Getting started opened from the service worker were an empty shell (Settings showed only its search
  box, Account its forms), with no banner, and their command palette opened every page and setting ("password" went
  to Account's password section, empty). Opened offline, each says so in the records page's banner ("Settings needs
  the server…"), with a link to the records kept on this device (or that it keeps none), and shows nothing that needs
  the server: not the page's own part, not the menus' links to other pages (they say why, as on the records page),
  not the palette's pages and settings. It opens itself once the connection is back. A request that can't reach the
  server says "You're offline. This needs the server." instead of "Failed to fetch", and a page that goes offline
  while it's open says changes need the server until it's back. The service worker keeps the new script
  (`sanguity-shell-v10`).
- **Dark and Light mode switch offline again** (#414): since #410 your menu's (and the palette's) Dark mode saved the
  account's theme, so offline it went back, saying it wasn't saved (or "Failed to fetch"). Offline it changes for this
  browser for now, says so, and is saved for the account once the server answers again.
- **The command palette waits only when it has to, and offers only what you can use** (#423): on the records page, the
  first after signing in, Enter pressed before its records were drawn opened the best of what was there ("cre" opened
  New profile) and "hem" said "Nothing matches" even once they had arrived; it waits for them there too, and the list
  is drawn again when they're in. Elsewhere Enter no longer waits when nothing still to come can beat the match
  ("account" opens Account at once), and one picked with the arrows stays picked as the records arrive. Someone who
  can only view the profile isn't offered Settings → Dashboard → Tabs, and the records page offline with no copy kept
  lists nothing that needs the server.
- **Units picked before another profile, the why's look, and an empty profile's tabs** (#425): lb then ft/in, then
  another profile picked at once, lost ft/in (the second save, waiting for the first's answer, was dropped with the
  switch); both are saved now, and the other profile is shown once they are. Units are yours, the same on every
  profile; a tick that's the profile's own is saved to the profile it was made on, its toast naming it. The note saying
  why a group isn't yours to use was green, as a save is; it's in the style of the line above it now. The records
  page applies an empty profile's own tab layout, as the other pages do.
- **Log out offline signs you out** (#422): offline, Log out went to the sign-in page as if it had worked, but the
  sign-in on the server was never ended, so opening the app back online was still signed in. Now the device's copy is
  deleted at once and the sign-in page says the sign-in on the server ends once this device reaches it again; until
  then the app opens on that page, which ends it as soon as the server answers (signing in waits for that) and says so.
- **Offline: the theme you picked, the connection coming back, and a heading** (#424): Dark or Light picked online
  (your menu, or Settings → Display) didn't reach the offline copy, so offline the records page went back to the
  theme the records were loaded with, and every page opened offline after it. The copy takes your own settings as
  they're saved now. The records page offline stayed "Offline · read-only" after the connection came back, with a
  theme picked offline unsaved, until a reload; it goes back to live data by itself now, as the other pages do, and
  saves the theme. A page opened offline had no visible heading; the banner carries the page's own ("Settings",
  "Records" when this device keeps none).
- **Body and activity's cards stay put on a phone as the fonts arrive** (#426): a chart card's heading beside its
  Chart | Table fit in the stand-in fonts and in the real ones, but not in Source Serif beside Inter's stand-in, so for
  a moment the control dropped under it and the cards below jumped (the browser check's font swap failed now and
  then). A heading beside a single control now wraps in its own column instead, which the control's height on a phone
  holds two lines of, so the swap moves nothing; at 320 px the longer headings sit beside the control too. The
  browser check lets Source Serif through first, so it catches that every time, and checks Body and activity at
  320 px too.
- **Offline: Settings → Display's theme, dates and units, and pages coming back by themselves** (#428): with a profile
  picked, Settings saves your theme, date format and units through the profile, and the offline copy didn't follow
  those saves, so the records page offline showed the theme, dates and units the records were loaded with (and dates
  offline were always in the browser's format). The copy takes them now, and the records page offline shows dates in
  your format. A page opened offline (Settings, Sources…) went live again only when the browser said it was online;
  it asks the server every few seconds too, as the records page does, so it opens once the server answers.
- **Log out offline: nothing left for the next person, and no other tab keeping you signed in** (#429): a theme
  picked offline waited on the device after Log out, and was saved for the next account signed in there; it goes at
  sign-out now, with anything else the sign-in left waiting. Another tab left open stayed signed in, and kept the
  sign-in on the server alive once back online; every open tab goes to the sign-in page as soon as you log out in
  one, and the sign-in on the server ends from there once it's reached.
- **An empty profile's theme, the palette's waiting Enter, and the banner by an open form** (#430): a profile with no
  records ignored the account's theme (Dark showed light); it's applied as with records. In the command palette, Enter
  pressed while the records were on their way, then an arrow key, opened the arrowed choice by itself once they came;
  the arrow takes the waiting Enter back, and the choice opens on its own Enter. On the records page, back online with
  a form open, the banner still said "Offline · read-only" while Save already worked; it says you're back online and
  that the latest records load once it's closed.
- **An account with no profile gets its own theme on the records page** (#433): the records page didn't apply the
  theme of an account with no profile, and sign-out left the last account's theme in the browser, so after someone
  with Dark logged out, the next account showed dark there whatever its own was. It's applied now, and sign-out
  leaves no account's theme behind.
- **Log out asks about every open tab's unsaved changes** (#432): Log out asked only about the page it was chosen on,
  so changes not saved in another tab (a name on Settings, a lab typed in on Sources) were lost without a word as that
  tab went to the sign-in page. It asks every open tab now, online or offline, and names those with changes not saved
  ("Changes not saved in Settings and Sources (other tabs) will be lost"); Stay keeps everything, and Log out sends
  every tab to the sign-in page, online too.
- **The Timeline's kind chips stay put as the fonts arrive** (#434): at 1280 and 1400 px the chips took a row more in
  Inter than in the stand-in font drawn before it, so everything below jumped 40 px once it arrived. Each chip's words
  now take the room they need in the stand-in, a little more, whichever font is drawn, so no chip moves; the browser
  check's font swap covers the Timeline at 1280, 1400 and 375 px.
- **Log out waits for a busy tab, and the first page after signing in has the account's theme** (#436): Log out asked
  the other open tabs for 150 ms, so one busy for longer (drawing, or a phone's in the background) wasn't named and its
  changes went without a word; it waits 600 ms now. And since signing out forgets the account's theme (#433), the first
  page after signing in started in the system's until the settings came; signing in now asks for the theme first.
- **Body and activity in four sections** (#276): its cards were in the order they shipped, so heart rate zones sat
  between steps and heart rate, and blood pressure three cards from heart rate; its tiles were in another order. Now
  it's **Body** (weight, body composition, BMI, growth), **Vitals** (heart rate and HRV, blood pressure, glucose,
  temperature, oxygen, breathing rate), **Sleep**, and **Activity and fitness** (steps and workouts, heart rate zones,
  VO2 max), each under its heading with its own tiles, which jump to its cards, and a bar of the sections above that
  jumps to each. A section with nothing in it is left out. Shading hospital stays is under "Show on charts", as on
  Labs, and the phone's last sync sits apart on the right.
- **The Overview grouped by question: today, labs, readings, the record** (#274): it opened on 12 lab tiles, 11 saying
  "In range", and Upcoming started 1,217 px down at 1400 px (2,694 px on a phone). It's in groups now, in the same order
  at every width: **Today** (today's doses, Upcoming with "in 8 days" and **Prepare a visit summary**, when the watch
  last synced), **Key labs**, **Readings** (the watch's, the scale's and the home readings in one row), the **Health
  record** (problems, allergies, immunizations) and **Recent**. Today's doses and the next appointment are on the first
  screen at 1400 and 390 px. The key lab tiles keep the person's order, grouped by panel, show a status only when it's
  high or low, and the header says once what the changes are since; it also lists what's outside its range at its
  latest result, a key lab not tested again since included, where Magnesium could say ▲ High above "Outside reference
  range at latest draw (0)". An upcoming appointment opens its details in the drawer. Recent lists clinical records
  only, with the watch's workouts summed in one line.
- **The Timeline: events by day, workouts folded, its filters beside the list, the journal a view of it** (#277): 97
  of the last year's 119 events were watch workouts, each row was 63 px tall with its kind about 1,100 px from its
  title and its date wrapping on a phone, and the list stopped at 600 rows, which ten years of records reach. Now the
  Events list groups a day's events under its date, said once (its number over its weekday) under the month's
  heading, with each event's kind small beside its title. Workouts stay on their lane and are left out of the list
  unless **List workouts** is on (or Watch is the only kind shown); listed, a day's several fold into one row ("3
  workouts · 2 h 10 m") that opens to each. The list draws 400 events at a time with **Show more**, and the browser
  skips the months off screen, so ten years list everything within the performance budgets. The seven count cards,
  which repeated the kinds' counts, are one line over the list: the lab draws, results and those out of range, and the
  workouts. The kinds and the search moved from the records bar, where they sat on every tab, into the Events card's
  header (on a phone, its Filters button opens the same sheet); the records bar holds only the period. And the
  journal, a card between the chart and its list, is a view of the tab: **Timeline | Journal**, as Labs has Chart |
  Heatmap, at `#timeline-journal`.
- **Labs grouped by panel everywhere, and one header above the charts** (#279): the Labs list put a lab under its
  panel only once it had 40 results, so the metabolic panel was "Less frequent" in the list and a panel of its own in
  the heatmap, and the Overview's tiles and Settings' key-lab list weren't grouped at all. One grouping now serves the
  list, the heatmap and its table, the Overview's key labs and Settings' key-lab list and its preview: each lab under
  the panel its results came in, the most measured first on Labs and in the order chosen elsewhere (a key lab moves
  within its panel), and the labs with no panel, or alone in theirs, in Other, last, collapsed in the list until
  opened. Above the charts, the view switch, Show on charts, a Legend and an ⓘ (the drag hint and how to zoom) share
  one row, with the not-medical-advice caption under it.
- **An import review screen: new, already here, or possibly the same** (#282): an import waiting to be saved said
  "Ready to import." over a bulleted list of counts, each unknown lab name had a list of about 60 series to scroll,
  and its button said only Save. The review now names the format and the dates ("C-CDA health summary (XML) · Jan 15,
  2025 – Jun 10, 2026"); has a tile per kind of record, its icon in the kind's Timeline colour, with a few of what's
  new in it (the server sends them with the preview, "samples"); a bar of how many records are new, already here and
  possibly the same as another source's; and the lab names to match as a table, each series typed with a list to pick
  from (a name that isn't a series is refused, saying so at its box). Its button, in a bar at the foot that stays in
  view, says what it does ("Add 6 lab results"), following the duplicates ticked; an upload that's all duplicates
  has nothing to add until one is. Duplicates and possible matches still act only when ticked, and nothing is saved
  before the button.
- **A Medications tab** (#275): medications showed in six places, but only a medication's drawer held one, so
  nothing answered "what is she taking now?". A tab after Body and activity has **Today** (each of today's doses,
  logged with Taken, Taken late or Skipped: the dose log lives here, and Add → Log a dose opens it), **Current** (since
  when, as listed, the last fill, with a link to its claims on Costs, and the reminders' times; a row opens its
  drawer), **Past** (closed until opened) and the medications over the records bar's period. It's hideable like any
  tab; a tab layout saved before it gets it after Body and activity, not at the end, on the server and in a copy kept
  offline. With six tabs, a phone's bar shows four and More, and from 1,100 px down some go under More.
- **The Medications tab: its Costs link, Past, today's doses in the lane, and offline** (#439): "Its claims on Costs"
  opened What you paid, empty, when Costs was last left there; it opens Claims now, with the drug's claims listed and
  focus in their search. Past closed again whenever the tab was drawn again (a dose logged, the window resized); it
  stays as it was left. A dose logged today fell outside the lane at 30 days, 1 year and 2 years, which end at the
  records' last day; the lane runs on to it. Its heading said "Over since diagnosis (Apr 14, 2025)"; it says the
  period: "Since diagnosis (Apr 14, 2025)", "The last 30 days". Offline, Taken, Taken late and Skipped each failed
  with "You're offline"; on the tab and the Overview they're disabled now, each saying why, as Add's Log a dose is.
- **The Overview's Today: a line per dose, a way to Medications for everyone, and groups that say what they'd hold**
  (#440): with reminders at 8:00 and 20:00 on three medications, six doses of about 85 px each pushed Upcoming under a
  phone's tab bar. Each dose takes one line now, with Taken as its one button and Taken late and Skipped under its ⋯,
  and the Overview shows the next three by time, from the latest reminder time that has come, then "3 more on
  Medications". The way to the Medications tab is there whoever is looking (a viewer with nothing logged today had
  none), drawn as Open Costs is, a button with its arrow, and a Ctrl-, ⌘- or Shift-click on it opens a new tab. A
  medication with no reminder time says "Any time" on the Overview as on the tab, not "Today" under Today. On a profile
  with little in it, Today, Readings and the Health record say what would be there, as Key labs did, rather than
  vanishing.
- **Help text: one line under a heading, the rest behind ⓘ** (#280): most cards ran heading, hint, "More about this",
  then content, with up to six lines of hint before the first control (Account's offline records) and 26 "More about
  this" toggles across the pages. Now a heading has at most one line of hint at 1,400 px. A card's background (how it
  works, what stays private, where its numbers come from) is behind an ⓘ beside its heading, named "About" and the
  heading, which opens the words under it as Labs' Legend does (a sheet on a phone) and closes with Escape, focus back
  on it; a card alone in its group has it beside the page's title. Steps read while doing something are in a
  disclosure named for them ("How to set up Health Auto Export on an iPhone"). What someone needs in order to act stays
  in full, at 14 px: what to upload, what a field expects, a warning before turning something on. Settings' own help
  splits the same way (a line break in the server's words), and no disclosure is labelled "More about this".
- **The Timeline's search says what its list says, and List workouts reads on while workouts are listed** (#444): a
  search only workouts matched ("walking") was said as "No events match.", while the list said 56 workouts were left
  out of it; now it's said as "Only workouts match: 56 workouts, left out of the list.", and a search that finds other
  events too adds the workouts not listed ("1 event matches" in the singular). With Watch the only kind shown, every
  workout is listed, yet List workouts read off and did nothing; it now reads on and can't be put off, its tip saying
  why. Body and activity's sync line moved out of the chart options with #442.
- **No Costs table scrolls sideways, and Show 200 more keeps focus** (#443): from 601 to 680 px the claims' eight
  columns didn't fit (Your share cut, dates on three lines), and at 375 px Prescriptions and every amount out of pocket
  still scrolled. Each table now follows the room its box has, so a phone held sideways or a docked drawer counts too:
  the claims are stacked rows under 680 px, and Prescriptions, the amounts out of pocket, HSA and FSA by year and the
  charts' tables under 560 px, each value named by its column. Show 200 more on the claims leaves focus on the first
  claim it adds. The Overview's Open Costs opens Claims, and Sources' "See them on Costs" after a payment opens What
  you paid, rather than the view last used.
- **A lab kept in two units shows each once** (#438): since labs were grouped by panel (#279), Hemoglobin in g/dL and
  in mmol/L, kept apart as two series, showed the mmol/L one twice and the g/dL one not at all in the Labs list, the
  heatmap and its table. The grouping keeps each series in its panel now, so each shows once with its own unit, the
  heatmap's grid naming the unit beside a name it shows twice.
- **A panel named one way everywhere, and Labs' charts in the list's order** (#441): a panel's name was in capitals on
  the Overview and the heatmap ("COMPREHENSIVE METABOLIC PANEL"), small capitals on Labs and in Settings, and Settings'
  preview of the key labs looked unlike the Overview. Now a name that came in capitals reads in sentence case, its
  abbreviations kept ("CBC with differential"), as a heading in the same style on the Overview and Settings' preview,
  and in the same words on Labs, the heatmap and its table. Settings' "Start from a condition" preview and Labs selected
  at first are grouped by panel too. Labs' chart view has headings (the list's, and its panels'), and its charts follow
  the list, under their panels' names, rather than the records' order. The heatmap cuts a long panel name short, whole
  in its tip, rather than run it into the grid; the Legend keeps 12 px from a phone's edge; and the key labs' change
  line keeps two lines' height under each panel again, so no row moves as the font arrives.
- **The records tabs read as one system: one heading scheme, the cost figures one way, every view in the address**
  (#442): the Overview and Body and activity had 18 px section headings over their cards while Medications, the
  Timeline and Costs titled their cards in 15.5 px, so "Today" was a section on one tab and a card on the next. Now
  every h2 on the six tabs is the same 18 px serif: a section's over its cards (whose titles are a step smaller), or a
  card's own on a tab of one card per question. The Overview showed Billed, Paid by insurance and Your share as tinted
  boxes, and Costs showed them the other way round as bordered tiles that looked like buttons; both now show **Your
  share, Paid by insurance, Billed by providers**, tinted in a card, as figures to read. Body and activity's charts
  have Labs' header: Show on charts, a **Legend** of the marks every chart shares (hospital stays, milestones) and an
  ⓘ, with the phone's last sync under it, out of the chart options' group a screen reader announced it in (#444).
  And Costs' Claims | What you paid is in the address (`#costs-paid`, kept on a reload), its switch named "Show the
  costs as"; picking a view on Labs, the Timeline or Costs adds a history entry as a tab does, so Back from the
  heatmap, the Journal or What you paid returns to the view before instead of leaving the tab.
- **Two units picked in a row and the page left at once keep both** (#453): lb then ft/in, then leaving before the
  server answered the first, still lost ft/in now and then (6 times in 80, in a loop). The save sent as the page was left (#419) went
  to the service worker first, which only passes the API on, and was lost when the page went before it had. The worker
  now routes the API straight to the network where the browser lets it (Chromium's static routes), so no API request
  waits for it.
- **Legends you can read: the hospital stay's swatch, forced colours, and Body's Legend kept open** (#450): the
  Hospital stay swatch on Labs, Body and activity and Costs' claims chart was its 7% shading alone, about 1.1:1 against
  the legend; it has the stay's edge now, 3:1 or more in light and dark. In forced colours the legends' swatches
  (Value, Reference range, Hospital stay, a Body card's lines, the heatmap's colours) were blank; they keep their
  colours, and the stay's edge is in the text's colour. Body and activity's Legend closed whenever the tab was drawn
  again (a resize, a dose logged) and left listeners on the window each time; its header is kept across redraws now,
  and a menu taken out of the page while open drops its listeners.
- **Lab names keep their abbreviations, and the Timeline, the Overview and Settings name and count labs as Labs does**
  (#451): a panel named in capitals lost its short abbreviations to sentence case ("Iron and tibc", "Sed rate (esr)",
  "Acth", "NT-probnp"); now a word of four letters or fewer, one with a digit and a hyphened one keep their capitals
  unless they're common words ("Iron and TIBC", "CBC w/ diff"). The Timeline's lab rows, their tips, the Overview's
  Recent and the drawer's results named panels in raw capitals; they use the same names as Labs. The Timeline's line
  said "45 lab draws, 100 results, 67 out of range", counting reports but values out of range; it counts the values,
  as Labs does ("442 results"). A key lab kept in two units showed the Overview tile of whichever came first, so its
  value could be stale; it shows the one with the latest result. Settings' Labs selected at first listed record names
  ("Automated Abs Neutrophil Cnt") where Key labs uses nicknames, and its ↑ and ↓ set an order nothing followed; it
  shows nicknames, with the record's name beside one, and Remove is each lab's one control.
- **Costs keeps focus, its rows work by keyboard, and its figures look alike** (#448): By month and By year, a type
  of care, a provider, a drug in Prescriptions and HSA only left focus on the page after Enter, and the
  out-of-pocket editor's Close did too; each keeps it now, Close giving it back to the row's date. What you paid's
  "What it was for" and "Who was paid" rows could be clicked but not reached by keyboard; they're buttons now that
  say whether they're picked. Its amounts scrolled inside a 420 px box (about three rows on a phone); they're in the
  page's own scroll, 200 at a time with Show more, and a row's editor opens under it rather than under the whole list.
  HSA and FSA by year came as an h2 before the view's own; the view's heading leads its figures now, with it an h3
  under it. The three claims figures sat two and one on Costs and unevenly on the Overview; on both they're three in a
  row where each has room, one under another where not.
- **Offline, nothing offers to save** (#449): #439 turned off today's dose buttons offline, but a medication's drawer
  still offered Log a dose, Add a reminder (a whole form) and a reminder's Edit, Turn off and Remove, and Costs'
  out-of-pocket editor its Paid with, Paid the claim and Save, each failing with "You're offline. This needs the
  server." Offline they're off now, a button saying "Needs the server: you're offline" as its tip and a field as its
  title, and a press or a form's submit does nothing.

- **An ⓘ stays open, with focus, when its tab is drawn again, and each is named and worded for its place** (#447): an
  ⓘ drawn by a tab got a new id each time, so on Medications, the Overview, What you paid and the Journal a window
  resized, a phone turned or a dose logged dropped focus to the page and closed the words it had open. Its id now
  comes from what it's about, so focus and an open ⓘ come back, and on a phone its sheet stays open as the phone turns,
  focus going back to the ⓘ once it closes. Settings' search finds the words behind a section's ⓘ ("Only exact
  conversions" shows Units, where it said nothing matched). The medications lane's ⓘ was named after the period
  ("About The last 2 years") and What you paid's after its heading ("About Out of pocket · the last 2 years"); they're
  "About the medications lane" and "About what you paid". The calendar feed's ⓘ sent reminders to "Timeline →
  Medications" and Today's ⓘ on the Medications tab sent you to that tab; they say "the Medications tab" and "below".
  Admin's provider directory says to run the download again now and then (Epic suggests weekly) in sight, not behind
  its ⓘ.
- **Views found in the command palette, Costs' view kept, and a modified click on "Its claims on Costs"** (#452): the
  palette found nothing for "heatmap", "what you paid" or "out of pocket", and "journal" only Add's Journal entry. Each
  view with an address is its own entry now (Labs › Heatmap, Timeline › Journal, Costs › What you paid), a title that's
  exactly what's typed ranks first, and Labs opens as charts, the Timeline and Costs in their first view, as their
  addresses do. Opening the app with no # restored Labs' and the Timeline's last view but not Costs'; it does now. "Its
  claims on Costs" took every click, so a Ctrl-, ⌘- or Shift-click opened nothing elsewhere; it leaves those to the
  browser, its address (`#costs?drug=…`) opening the drug's claims in the new tab.
- **The Timeline's search finds a lab draw by its tests, and counts only theirs** (#459): a lab draw was found by its
  panels' names, so "ferritin" or "hemoglobin" listed nothing; it's found by its tests' names too, as the records and
  Labs name them ("platelets" as well as "platelet count"). Under a search that names tests, the line over the list
  counted every value of the draws found, so "psa" said "23 lab draws, 598 results, 8 out of range" when PSA's 23 were
  all in range; it counts those tests' results only, and says which ("23 lab draws, 23 PSA results"; "104 results of
  the 4 tests matching “iron”").
- **Costs' figures fit, its lists scroll with the page, their headings stay in sight, and a picked row shows in forced
  colours** (#456): in a French browser at 1280 px the Overview's "1 002 666 $US" ran past its tile; on the Overview
  and Costs the three figures go one under another wherever one doesn't fit its third. Prescriptions, HSA and FSA by
  year and the charts' tables scrolled inside boxes of their own (taller than a sideways phone's screen); they're in
  the page's scroll now, and a table's column headings stick under the top bar as it scrolls by. In forced colours a
  picked type of care or "What it was for" row looked like the rest and the rows' bars vanished; the picked row is
  ringed and the bars drawn. Switching to What you paid is kept for the next visit at once, not only once something
  else saved. An address naming a drug's claims (`#costs?drug=…`), opened in a new tab or typed, shows them as the
  link's click does: scrolled to, focus in their search. What you paid's ⓘ stays beside its heading at 320 px, and
  offline the medication drawer and Costs' editor say in a line why they can't save.
- **Labs selected at first takes a nickname, panel names keep W/O and lose capital short words, and a lab day counts
  its values** (#458): Settings' Labs selected at first listed nicknames but Add knew only record names, so a nickname
  added a lab that didn't exist under an "Other" of its own, which Labs then dropped. Add takes the name a lab is shown
  by or its record name now, suggests the names shown, and refuses one the profile has no lab by, as Key labs does
  (which takes nicknames too now); within a panel the labs are in Labs' order, the most measured first. A panel's name
  in capitals split "W/O" ("CBC w/O diff") and kept common short words in capitals ("TYPE and screen", "Anion GAP",
  "Probnp"); "W/O" is one word, the common short words are in sentence case, and a word that doesn't read as one stays
  as written ("Type and screen", "Anion gap", "PROBNP"). The importers cut a lab day's line at 160 characters mid-name
  ("…, Hepatic PA"); they cut at a whole name with "…", and a line cut before is shown without the half name. A lab
  day's title counted its reports ("2 lab results" over a Results fact of 11 values, in the drawer, on the Timeline
  and in the Overview's Recent); the FHIR, C-CDA and Epic MyChart importers and the demo count its values, as the CSV,
  PDF and CareSpace ones did ("1 lab results" there is "1 lab result"), and the drawer counts a day imported before
  again once its results are in.
- **Focus stays where it was on Medications and moves with Body's tiles** (#455): Taken on an "Any time" row of
  Medications' Today put focus on the medication's name, where the Overview kept it on Taken: a part drawn again gave
  focus to the first control of the same name in the page, and the Overview, hidden but still there, had one first.
  It goes to the one shown now, on any tab. A Body and activity tile scrolled to its card but left focus on the tile,
  far above on a phone, so the next Tab went back up; focus goes to the card's heading, as the section bar's does.
- **A setting picked just before leaving Settings stays picked** (#457): as the page went, the save still on its way
  failed in the page though its request reached the server, and the page took it as refused. A theme picked just
  before leaving went back in this browser (Light picked, the next page showed dark), the page said offline for a
  moment, and a second unit picked was sent twice. Once the page is being left, a save that fails does nothing more
  there, and nothing queued behind it is sent again.
- **Body and activity's tables a page at a time, Key labs' hint on one line, Today's ⓘ named one way** (#460): a
  card's Table drew every day; on ten years Heart rate's had 3,493 rows, and every card on Table about 64,000
  elements. Each shows its latest 200 rows, with Show more for the next, as Costs' list does. The Overview's Key labs
  had a four-part hint, three lines on a phone and "click" on touch screens; it says the latest draw and the changes'
  date, the rest behind its ⓘ. Today's ⓘ was "About Today" on Medications and "About Today's medication doses" on the
  Overview; it's "About today's medication doses" on both.
- **The command palette's events are the Timeline's, a lab renamed is found at once** (#463): "cbc" listed the lab
  results the Timeline hides inside their draws, in the records' capitals ("CBC WITH DIFFERENTIAL · Lab result"), and
  offered "Search the Timeline" with a count of them all ("254 events in all" for the Timeline's 45). It lists what the
  Timeline shows, a lab result's title as Labs names its panel, and its count is the Timeline's in its period
  ("Everything on file: 45 events", "The last year: 9 events"; on another page, "45 events on file"). Off the records
  page it found a lab draw only by its panels and not its kind, so on Settings "hemoglobin" found the lab but no draws;
  it finds them by their tests' names, as the Timeline does. A lab renamed on Labs wasn't found by its new name until
  the page loaded again, and its old name still found the draws; the search words change with the name.
- **A Timeline search's draws count its tests out of range, the heatmap table keeps its panels' names, and a long
  test name can't fail an import** (#464): under a search that names tests, a lab draw's "out of range" badge, its ▲
  and its tip counted every test of the draw, so "psa" on ten years, its line saying no PSA was out of range, showed
  "2 out of range" and a ▲ on 7 draws; they count those tests' only, as the line does. The heatmap's Table, scrolled
  sideways, showed each panel's heading row as an empty band while the labs' names stayed; the panel's name stays at
  the left of its row too. The importers failed on a lab day whose first test name was 158 or 159 characters long with
  others after it; it's kept whole with "…".
- **A test search counts each draw's own results, and the command palette off the records page counts the Timeline it
  opens** (#470): a lab draw's search words, badge and ▲ came from every result on its day, so with an outside lab's
  Creatinine 1.6 H typed in on a clinic draw's day, "creatinine" showed "1 out of range" and a ▲ on both draws, and
  "wbc" listed the outside lab's creatinine-only one; they come from the draw's own results. Off the records page the
  palette's "N events on file" opened the Timeline on its saved period (Settings' "psa · 23 events on file" opened
  "Events (0)" on ten years); it opens on everything on file, in the kinds the Timeline shows. There it now finds the
  watch's workouts and ECGs, journal entries and cycle tracking's, as the Timeline does ("walk" found nothing on
  Settings), each with its kind's icon, and a lab renamed in Settings the next time it opens. On Getting started its
  events read "Lab draw · 1729327440"; they have dates.
- **Focus follows a pick on Costs, and stays put on Labs' Clear, Defaults and ×** (#462): a type of care, a provider,
  a drug, or a What it was for or Who was paid row picked on Costs scrolled to its list but left focus on the row, now
  above the screen, so the next Tab went back up; focus goes to the claims' heading, or the amounts' first control, in
  sight. Labs' Clear and Defaults, and a chart's ×, left focus nowhere; Clear and Defaults keep it, and × gives it to
  the next chart's ×, or, on the last chart, to that lab in the list.
- **Costs' claims stack wherever they don't fit, and What you paid's hint is one line** (#465): the claims became a
  stacked list under 680 px of box, but in a French browser they need about 720, so from 748 to 776 px the table ran
  past its card and the page scrolled sideways. The table's width is measured, and it stacks wherever it doesn't fit.
  What you paid's three-sentence hint says where the amounts came from ("From a finance export and claims' shares");
  the export's date and what isn't counted are behind its ⓘ.
- **Costs' claims stack as they're drawn, a claim's row stays in sight as the drawer closes, and What you paid's hint
  is one line with all its sources** (#467): since #465 the claims stacked a frame after they were drawn, so Show more
  on a phone left the claim it focused 300 to 4,500 px below the screen, and a claim opened as the drawer docked in a
  French browser ended under it; they stack in the frame they're drawn. Closing the docked drawer at 1300 or 1400 px
  gave focus to the claim's row, then laid the page out wider with that row above the screen; it's brought into
  sight. With a payment typed in, the hint was two lines at 390 px; it names the three shortly ("From payments typed
  in, an export and claims"), in full behind its ⓘ.
- **The Overview's Today keeps a medication's name readable once a dose is logged** (#468): each dose logged today was
  added beside Taken and ⋯ on a row that couldn't wrap, so the name was squeezed a word a line under them, to nothing
  with two doses at 900 px or six at any width. A row with doses logged wraps them under its name, as the Medications
  tab's does; a row with nothing logged is still one line.
- **Labs on a phone: the heatmap's table shows its values, its dates stay on top, and Clear stays in sight** (#469):
  the table's lab names took 256 px, leaving 14 px for the values at 320 px and 84 at 390, so no date's column was
  ever seen whole. On a phone the names wrap, the ✎ and ? together under them, in a column of about 40% of the box.
  Scrolled down, the values showed on top of the dates; the dates are on top. On a phone held sideways, Clear after ×
  on the last chart shrank the page and left the focused Clear above the screen; a redraw that moves the focused
  control out of sight brings it back. The empty charts said "Pick lab values on the left", where on a phone the list
  is above; they say "in the list".
- **The heatmap shows each draw's result on a day with two, and opens the draw that holds it; the Timeline's line
  counts a draw's own results under a search** (#471): with an outside lab's Creatinine 1.6 H typed in on the day of a
  clinic draw whose Creatinine was 0.89, the heatmap showed only the 1.6, in its grid, tip and table, and its cell
  opened the clinic's draw. The cell holds both side by side, each coloured and marked by its own range, each's tip
  saying the other; each opens its own draw; the table gives both, each with its time. Under "metabolic", listing the
  clinic's draw only, the line said "7 results …, 1 out of range" from the 1.6 on its day; it counts the listed draws'
  own results, as their badges do.
- **Journal entries after the latest record are in the Timeline's period, the palette leaves your period as it was,
  and a profile with only a journal shows it** (#472): the periods end at the latest record, which was the latest
  import's or the watch's, so a symptom logged after the last import was outside "Last 2 years" ("Events (0)") and,
  two weeks later, outside All too; the journal's and cycle tracking's entries count, as soon as they're saved.
  Following the palette's "Search the Timeline" from another page saved All as your period for every tab; it shows
  everything for that visit and keeps yours. Its count said "0 events" for "walk" with the walking workouts listed
  above it; it says "· 65 workouts, not listed", as the line over the list does. A profile with journal entries and
  nothing imported said "no records yet", and the palette found nothing there; it opens on the Timeline's Journal view,
  a first entry saved there shows at once, and the palette finds its entries.
- **Costs: a payment's editor opened on a sideways phone keeps its date in sight, and a claim opened as the drawer
  docks is in sight whole** (#473): Enter on a payment's date opened an editor taller than the window and lined its top
  up with the window's, so the date that had focus went 10 to 34 px above the screen at 568×320; the date's row comes
  into sight first, then as much of the editor under it as fits. A claim opened as the drawer docked at 1300 or 1400 px
  brought only its name into sight, at the window's bottom edge, its date, provider and amounts cut off (42 px of a
  stacked row in a French browser); its whole row is brought into sight.
- **Links that widen the view leave yours saved, and All lists an entry dated ahead** (#475): a medication's "Its
  claims on Costs" (and its address) saved All as your period, on every tab and after a reload, when its claims were
  outside a saved 30 days or a zoom; the Overview's "All N on the Timeline" for immunizations saved All and
  immunizations alone as the Timeline's kinds. Both show what they link to for that visit and keep your period and
  kinds. All ended at today, so a note dated next year was listed under no period; All runs to the latest entry,
  however far ahead, and the other periods still end at the latest record.
- **A profile with nothing imported leads with Get started, one with only a phone opens on Body and activity, and the
  first entry's save says so** (#477): with a journal, the "no records yet" card and its Get started had gone, and
  nothing on the page led to Upload or the guide; the Overview opens with the guide, Upload files, Connect a phone and
  Type records in, whatever there is of a journal or a watch. A profile with a phone connected and nothing imported
  said "no records yet", even at Body and activity; the watch's data counts as its records, and it opens there. The
  first entry on an empty profile reloaded the page with focus nowhere and nothing said; it opens the entry in the
  drawer, as a save with records does, and says "Entry saved." The palette's line for a search says "with these
  filters", as the line over the list then does.
- **On phones the heatmap fits its box, chart tips stay on the screen, and What you paid's dates stay clear of its
  headings** (#476): at 320 px the heatmap's grid was laid out 320 px wide in a 270 px box, so its right 50 px (the
  latest columns, and a month's label) couldn't be seen, scrolled to or tapped; it's laid out in its box's width. A
  chart's tip that didn't fit right of the pointer went left of it with no limit, 128 px off the screen's left edge
  from a heatmap cell at 320 px; tips stay 8 px inside the screen, above or below the pointer where they fit on
  neither side. On a phone held sideways What you paid's headings wrap to 63 px, past the 40 px its dates were kept
  clear of, so Shift+Tab up the dates, or the editor's Close, left a date under them; the dates are kept clear of the
  headings' measured height. On the ten-year All at 1400 px, a day with two draws split its cell into parts too narrow
  for a ▲, with no word of it; the legend's "zoom in" hint shows then too.
- **A new account's first journal entry opens, a year typed wrong doesn't squeeze the charts, a link's kinds stay on
  the Timeline, and a tap on the heatmap reaches its cell** (#479): on a new account's empty profile, Add → Journal
  entry (and Log a dose) was sent to the guide before its form could open; an address naming Add's action skips the
  guide. A journal entry dated 2207 ran All to 2208, ten years squeezed into each chart's left edge; the person's own
  entries move All's end no more than a year ahead, and the date asks for no later. After the Overview's "All N on the
  Timeline" for immunizations, Labs drew no medication lanes or journal marks though they were ticked, for the rest of
  the visit; the kinds are the person's again once the Timeline is left. The heatmap's month lines, drawn over its
  cells, took over half the taps on a phone's ten-year All; they take none. A profile with no hospital stays isn't
  offered them on Body and activity.
- **A year typed past the journal date's limit is refused, the heatmap's month lines stay under its colours, Escape
  after the palette keeps focus, and Costs' notes keep their line** (#481): the journal's date limit only bounded its
  picker, so 2207 typed in still saved, then was listed under no period; the entry and symptom-group forms say to
  check the year, and the server refuses a date more than a year ahead. On a phone's ten-year heatmap the month lines,
  drawn over sub-pixel columns, hid a month's first draw and its out-of-range colour; they're drawn under the cells,
  and the latest month is named on the axis. A record opened from a tab's link at 1400 px docked the drawer, which put
  that tab under More; Escape left focus on the page once More went, and now it's back on the tab. Costs' largest
  group's bar squeezed its note onto two lines; the bars take what the note leaves.
- **Costs' bars share one scale again, and the heatmap names its latest month** (#483): sized against what each note
  left, a row's bars stopped comparing with the next (Vision's $6,963 shorter than Procedures' $6,961); every row's bars
  run the row's whole width, with the note under them. On ten years the heatmap's latest month was still left
  unnamed when it fell close to the label before it; it's always named now, the one before it dropped.
- **The heatmap's axis labels don't meet, and What you paid's notes keep their line** (#485): in a period with one draw
  day the latest month was pulled back over "Draw date" ("Draw dateOct 26"); it stays at the grid's start, the axis
  as wide as it needs. Labels are spaced by their measured width too, so French ones ("sept. 25oct. 26") don't meet.
  What you paid's What it was for and Who was paid put each note under its bar, as the claims' lists do, so it no
  longer wraps on a phone.

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

From an install with connected sources (before #228):
- To offer immunizations, allergies, pharmacy fills and appointments, add their scopes to each source under Admin →
  Connected-source apps (the source says which kinds it leaves out; the walk-through lists the scopes) and their types
  in the app's registration at the vendor. Existing connections keep pulling what they did, and ask to sign in again
  only when someone turns a new kind on.

From an install whose phone already sent blood glucose (before #181):
- Nothing to do: on its first start after updating, the receiver reads the glucose in the uploads it has archived in
  `raw/`, once, and writes the summary again. For a CGM in Health Auto Export, add an automation with blood glucose
  alone and no time grouping (Sources → Phone and watch); readings grouped into hours are left out.

From an install whose phone may have sent cycle records (before #183):
- Nothing to do: on its first start after updating, the receiver rewrites each upload archived in `raw/` without any
  menstrual cycle record, sexual activity or basal body temperature it holds, once, as cycle tracking starts off for
  every profile.

From an install with your own compose file or template (before #227):
- For backups to include the phone and watch data, the app needs the receiver's folder read-only: in `compose.yaml`
  add `- ${WATCH_DIR:-./watch-data}:/srv/watch-store:ro` and `SANGUITY_WATCH_STORE: /srv/watch-store` to the `app`
  service; in `compose.release.yaml` mount `- ${WATCH_DIR:-./watch-data}:/watch:ro` in place of the `/watch/public`
  line. Until then **Admin → Backups** says the backups don't have it. Restoring now stops the receiver too
  (README, Backup and restore).

Moving an install from a git checkout to the published image: see [Upgrading to a release](README.md#upgrading-to-a-release).

[Unreleased]: https://github.com/sanguity/sanguity/commits/main
