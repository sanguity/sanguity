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
- **Sign-in and setup get the app's focus ring** (#310): Sign in, Sign in with a passkey and Create account had the
  browser's thin black or white ring. The reset and the one focus ring moved from `app.css` to a small
  `www/css/base.css` that every page loads after the tokens, sign-in and setup too, so their buttons and fields are
  ringed in the accent, 2 px out, like the app's; their own copies of the box-sizing reset and of a field's ring are
  gone. `tests/test_css_tokens.py` fails if a stylesheet or a page rings focus again for every element.

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
