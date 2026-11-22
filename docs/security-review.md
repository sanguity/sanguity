# Security review against OWASP ASVS

The review from issue #8, of Phase 1 (#79, #81), refreshed in #203 for everything added since: uploads and
imports, typed-in records and corrections, settings, connected sources and their sync controls, outside
connections, SMART Health Links, the provider directory, calendar feeds, the journal, medications, costs, the visit
summary PDF, share links, phone and watch uploads per profile (Health Connect and Health Auto Export), offline
records on a phone, the Sources, Sharing and Admin pages, backups in Admin and the first-run guide.

- **Standard:** OWASP ASVS 4.0.3, Level 2. The chapters: V2 authentication, V3 sessions, V4 access control, V5
  validation and encoding, V6 stored cryptography, V7 errors and logging, V8 data protection, V9 communications,
  V12 files, V13 API and V14 configuration. Level 3-only requirements are left out.
- **Code reviewed:** `main` after Phase 6's first wave (#199, #200, #165, #201, #58, #213 and #34), plus the fixes in
  the branch for #203. Every route in `server/main.py` and `server/routes_*.py`, and the modules behind them.
- **Reviewed:** 17 November 2026 (#203). Phase 1 was reviewed on 15 November 2026 (#79, #81).
- **In scope since #203:** the watch receiver's checking of each profile's secret (`watch/receiver.py`, #46, #148).
- **Out of scope:** `tools/anonymise.py` (#34) is a tool you run on your own computer, on your own export, before
  sharing a sample; the server never runs it and it opens no connection. Its limits are in
  [anonymising-exports.md](anonymising-exports.md). It parses XML with expat, with entity declarations refused, and
  refuses unsafe zips (`tests/test_anonymise.py`).

**Results**

| Result | Meaning |
|---|---|
| **Met** | The requirement holds. The reason says where in the code. |
| **Fixed** | Not met on `main` at Phase 1. Fixed in the branch that added this document (see [Fixes made](#fixes-made)). |
| **Fixed (#203)** | Not met on `main` before #203. Fixed in its branch, with the test named (see [Fixes made in #203](#fixes-made-in-203)). |
| **Issue #n** | Not met. The work, or the decision, is in that issue. |
| **Accepted** | Not met as written, on purpose for a self-hosted household app (see [Accepted deviations](#accepted-deviations)). |
| **N/A** | Sanguity has no such feature. |

## Summary

Phase 1 held up well, and the features since keep to its patterns. Every route that touches a profile checks the
caller's role on it, and a profile that isn't shared with you is reported as not found, admins included. Every
record, file and secret added since is encrypted at rest. The three ways in without a session (a share link, a
calendar feed, the phone's upload secret) are random 256-bit tokens of which only a hash or a sealed copy is kept,
each revocable and each scoped to one profile. Every request to another server goes through one gateway
(`server/net.py`) that refuses any host nobody connected and records each contact.

The refresh found three gaps in the code, all fixed with tests, and older statements in this document that later
features had made untrue:

| Severity | Finding | Result |
|---|---|---|
| Medium | nginx's access log, on by default in the `compose.yaml` install, recorded each request line: an uploaded file's name (`?path=`), a provider search (`?q=`), a document's own file name and a connected source's sign-in code (`/connect/return?code=`). | **Fixed (#203)**: `access_log off` |
| Medium | Wrong share-link and calendar-feed tokens were throttled per address, and a waiting address was refused before its token was even looked up. Behind a reverse proxy everyone shares its address, so ten wrong tokens from a stranger shut every share-link holder and calendar app out for up to 15 minutes, again and again. | **Fixed (#203)** |
| Low | A failed import, a failed pull or an error in a request wrote the exception's message to the app's log. An importer's message names your file ("Oncology visit.xml isn't well-formed"), and an unexpected `KeyError` can quote a record. | **Fixed (#203)** |
| Low | This document said the app makes no outbound requests, takes no uploads and caps every request at 64 KB. All three stopped being true in Phases 2-4. | **Fixed (#203)**: V5, V9 and V12 below |

Ten smaller points are accepted as they are, with why ([Accepted deviations](#accepted-deviations), #203), and
four are written up as [follow-ups](#follow-ups).

## Who can reach what

Roles are per profile: owner, editor, viewer. Admin is an account flag for managing accounts and the install; it
gives no access to anyone's records. A share link and a calendar feed carry their own token and no session.
`tests/test_security_refresh.py` lists every API route with what it needs and fails when a route is added or its
check changes without updating the list, and it exercises this table against a live app (`Reach.test_the_access_table`).

| What | Viewer | Editor | Owner | Admin (not a member) | Share-link holder | Calendar feed |
|---|---|---|---|---|---|---|
| A profile's records: Overview, Timeline, Labs, Body and activity, Costs, Medications, documents; a record's details by `GET /api/profiles/{pid}/details?ids=` when it's opened (#217) | Read | Read | Read | No (404) | Only the sections, labs and dates the link was made with; no documents, report text or settings | Upcoming appointments and expected lab draws, minimal by default |
| A watch's ECG recordings and irregular rhythm notifications (#182): the events with the records, a recording's samples by `GET /api/profiles/{pid}/watch/ecg/{name}` when its drawer opens | Read | Read | Read | No (404) | Only if its owner ticks "ECGs and irregular rhythm notifications" (#225, unticked by default): the date and what the device said, never the samples | No |
| The journal | Read | Add, change, delete | Add, change, delete | No | If the link includes it (without who wrote what) | No |
| The journal's photos (#161): `GET /api/profiles/{pid}/journal/photos/{id}`, sent first by `PUT …/journal/photos` | Read | Send, attach, remove | Send, attach, remove | No (404) | Only if its owner ticks "Photos in the journal" too (unticked by default; offered only when there are some), each fetched with the token like the view | No |
| The medication log (#235): `…/medications/doses` | Read | Log, correct, remove | Log, correct, remove | No | Only if its owner ticks "Medication log" (unticked by default), in the link's dates, notes included | No |
| Medication reminders (#162): `…/medications/reminders` | Read | Set, change, turn off, remove | Set, change, turn off, remove | No | No | Each time as a recurring event, "Medication" unless the feed is set to full details; on unless the feed's person turns them off |
| The phone's and watch's measures in a visit summary or a share link (#225): home blood pressure, glucose, temperature, blood oxygen, respiratory rate, VO2 max, immunizations, BMI and growth | Read (summary) | Read (summary) | Read (summary; ticks them for a link) | No | Only the measures its owner ticked (each unticked by default), in the link's dates; cycle tracking never | No |
| About this person: birth date, sex used for reference ranges, height history (#194) | Read | Change; add and remove heights | Change; add and remove heights | No | Only if its owner ticks About this person (unticked by default) | No |
| Import history | Read | Undo, rename | Undo, rename | No | No | No |
| Upload files, import (preview, save, discard), type in records, corrections (with similar entries and category rules, #237), "same result", cost marks | No (403) | Yes | Yes | No | No | No |
| Export everything (zip): the activity log in it only for an owner (#589) | No | Yes (no activity log) | Yes | No | No | No |
| Connected sources: see a profile's | Yes | Yes | Yes | No | No | No |
| Connected sources: connect, sync now, choose what's pulled, fetch an attachment, disconnect | No | Yes | Yes | No | No | No |
| Phone and watch: the upload secret and setup | No | Yes (a new secret replaces the old) | Yes | No | No | No |
| Cycle tracking (#183): see its records and setting | No (403; not told it exists) | Yes | Yes | No | Never | No |
| Cycle tracking: turn on or off, delete all of it | No | No | Yes | No | No | No |
| A SMART Health Link pasted on Sources | No | Yes (after confirming) | Yes | No | No | No |
| The visit summary PDF and its questions draft | Yes | Yes | Yes | No | No | No |
| Settings: read; their own settings | Yes | Yes | Yes | Their own only | No | No |
| Settings: the profile's (tabs, ranges, milestones, ...), and starting them from a condition's preset (#186, `GET /api/profiles/{pid}/condition-presets`) | No | Yes | Yes | No | No | No |
| A calendar feed of the profile for themself | Yes | Yes | Yes | No | No | No |
| Everyone's calendar feeds of the profile: see, revoke | No | No | Yes | No | No | No |
| Share links: make, list, revoke | No | No | Yes | No | No | No |
| Who has access: see | Yes | Yes | Yes, and whose account an admin has disabled (#314) | No | No | No |
| Who has access: share, change roles, remove someone | Leave only | Leave only | Yes | Repairs a profile with no active owner only | No | No |
| The profile's activity log | No | No | Yes | No | No | No |
| Rename or delete the profile | No | No | Yes | No | No | No |
| Outside connections (Admin page) | Empty list | Their profiles' entries | Their profiles' entries | The whole install | No | No |
| Accounts, require two-factor, sign-in notices, account activity | No | No | No | Yes (with their own second factor) | No | No |
| Connected-source apps (vendor setup), lab-name matches, install settings, backups | No | No | No | Yes (with their own second factor; the most powerful changes also need a fresh check) | No | No |
| The provider directory | Yes | Yes | Yes | Yes | No | No |
| "What is this test?" pages (#163, `GET /api/lab-test-pages`): the app's own table of links, nothing of anyone's | Yes | Yes | Yes | Yes | No | No |

Someone signed in to an account a profile isn't shared with gets 404 on every route of that profile. A share link
or a feed address opens nothing but itself: with it and no session, every other route answers 401, and a feed's
token is not a share link's token or the other way around (`Reach.test_a_share_link_or_feed_opens_only_itself`).

## Fixes made in #203

Each has a test.

| Fix | ASVS | Change | Test |
|---|---|---|---|
| No access log in nginx | 7.1.1, 7.1.2, 8.1.3, 13.1.3 | `nginx.conf` sets `access_log off` for the whole server, as the released image already did (`docker/nginx-main.conf`) and as the app does (uvicorn's access log is off). Sign-ins, refusals and every view of a record stay in the app's security log and each profile's activity. | `tests/test_security_refresh.py` `Nginx.test_no_access_log` |
| A working token isn't shut out by others' wrong ones | 2.2.1, 11.1.4 | `POST /api/share/view` and the calendar feed look the token up first. A token that opens something is served whatever its address's count of wrong ones (it still counts toward its own link's or feed's allowance); a wrong one from a waiting address still waits. With 256-bit tokens, slowing guesses at the address protects nothing a working token could be denied for. | `tests/test_share_links.py` `test_throttled`, `tests/test_calendar.py` `test_wrong_tokens_are_throttled` |
| Errors logged without their message | 7.1.2, 7.4.1 | `main.QuietTracebacks` on the `sanguity` loggers (imports, pulls, backups, importers) and on uvicorn's error log (an error in a request): each exception is logged with its type and where it happened (file, line, function, the line of code), never its message or its cause's. The person is still told why an import failed, encrypted in `jobs.message`. | `QuietLogs` in `tests/test_security_refresh.py`, including a real failed import whose file name stays out of the log |
| Every route listed with what it needs | 4.1.3, 4.2.1, 13.1.4 | A test of every API route's session dependency and the least role its `need()` checks; a new route fails it until it is added there and to the table above. CONTRIBUTING says so. | `Routes` in `tests/test_security_refresh.py` |
| Every column encrypted or plain on purpose | 6.1.1, 8.3.4 | A test that every text column of every table is in `atrest.FIELDS` or listed as plain with why, and that the settings kept in plain text are the reviewed ones (how things look, dates, units, the guide's state, the install's sync rules). | `AtRest` in `tests/test_security_refresh.py` |

## Fixes made

The Phase 1 fixes. Each has a test in `tests/test_api.py` or `tests/browser_check.py`.

| Fix | ASVS | Change |
|---|---|---|
| Security event log | 7.1.3, 7.1.4, 7.2.1, 7.2.2, 2.8.5 | Each security event becomes one JSON line on the app's output, so `docker compose logs app` shows it. The events: sign-ins and how they were made, failed passwords, codes, recovery codes and passkeys, a reused authenticator code, throttled tries, a wrong password when re-checked, refused access to a profile or an admin route, refused cross-site requests, and (since #47) a refused share-link token. The lines never hold a password, code or token. They name a username only when it matches an account, because a name that matches none may be a password typed into the wrong field. JSON encoding means a username can't add lines to the log (`main._audit`). |
| No inline script | 14.4.3, 5.3.3 | The pages' CSP is `script-src 'self'`, without `'unsafe-inline'`. The dev server sends the same CSP (`main.PAGE_CSP`), and a test checks that it matches `nginx.conf`. The browser check runs every page under the real CSP and expects no violations. |
| One-time passwords expire | 2.3.1 | A one-time password from an admin stops working after `TEMP_PASSWORD_DAYS` (7 days by default). Schema version 7 adds `users.temp_pw_expires`. |
| `__Host-` session cookie | 3.4.4 | Over HTTPS the session cookie is `__Host-sg_session`. Plain HTTP can't use the prefix, so there it stays `sg_session`. A cookie from before the change is still read, and the next HTTPS sign-in replaces it. |
| API answers download | 14.4.2 | Every `/api/` answer that doesn't set its own carries `Content-Disposition: attachment; filename="api.json"`. Documents say `inline`. |
| Zip size limit | 12.1.2 | `server.manage import` refuses an export zip that unpacks to more than 100,000 files or 16 GB, before writing anything. |

## Issues filed

All five are in the Phase 1 milestone.

- #74 Pepper password hashes and strengthen recovery codes (2.4.5, 2.6.2): fixed; the pepper is kept encrypted with the data key, and recovery codes stay 8 characters, now salted
- #75 Second factor for admin actions (4.3.1, 4.3.3): done, admins need a second factor (a one-person install is exempt) and a fresh check before the most powerful actions
- #76 Decide session lifetime defaults (3.3.2): decided, see [Accepted deviations](#accepted-deviations)
- #77 Automatic dependency updates, and an update path that pulls patched images (14.2.1): done, see 14.2.1
- #78 Confirm or change the accepted ASVS deviations (2.1.1, 6.4.2, 8.1.4, 9.1.1, 9.2.2, 14.4.5): decided, see [Accepted deviations](#accepted-deviations)

## Accepted deviations

Decided by the owner in #78, and for 3.3.2 in #76:

| # | Decision | Why |
|---|---|---|
| 2.1.1 | Accepted as is: 15 characters, or 8 with a second factor | NIST SP 800-63B's rule. A short password must be replaced if the second factor is removed. |
| 6.4.2 | Accepted; the README documents keeping the key in a secret store | A vault or HSM is unusual on a home server. `SANGUITY_KEY_FILE` can name a Docker/Podman secret or a systemd credential, since those are files too. |
| 3.3.2 | Changed (#76): 12 hours without a second factor; 30 days with one, and no idle limit, accepted | On a shared household tablet, signing in again every 12 hours or after 30 idle minutes would be constant friction. A second factor stops a stolen password, so long sessions are kept for accounts that have one; password-only accounts get the 12 hours ASVS asks for. All three limits can be changed in `.env`. |
| 8.1.4 | Changed: admins see bursts of failed sign-ins | `alerts.py`, `/api/security/notice`, shown on the Admin page. Per-endpoint rate limits for signed-in requests stay out of scope. |
| 9.1.1, 9.2.1–9.2.2, 14.4.5 | Accepted: HTTPS is the proxy's; plus a plain-HTTP warning and proxy guidance | The pages warn when opened over plain HTTP from outside the home network or a tailnet; the README covers `COOKIE_SECURE=1` and HSTS. nginx to the app stays on the compose network. |

Found in #203, each low, and accepted for the reason given:

| # | What | Why it's accepted |
|---|---|---|
| A1 (9.1.1) | Health Auto Export sends the profile's secret itself (`Authorization: Bearer`), since it can add a header but not sign; the default upload address is plain HTTP on `RECEIVER_PORT`. Someone who can read that traffic could send uploads to the profile. Health Connect signs each upload instead, so its secret never travels. | The same decision as 9.1.1: HTTPS is the proxy's. The receiver can sit behind your HTTPS proxy, and `WATCH_UPLOAD_URL` then gives the phone that address (README, Phone and watch). An upload can only add watch data to that one profile; it reads nothing back. Making a new secret on the Sources page stops the old one at once. |
| A2 (4.1.3) | An admin who isn't a member still sees two things about profiles: on the Admin page's outside connections, the host of each SMART Health Link and connected source a profile contacted (which can say where someone is treated), and in lab-name matches, the lab names confirmed on imports and who confirmed them. | Both are install-wide controls that are the admin's job: every outside contact must be visible to whoever answers for the install (#59, #97), and lab-name matches apply to every profile (#23). Neither shows a result, a record or a document. |
| A3 (6.1.1) | A connection's source name and FHIR address (`connections.source`, `issuer`, `fhir_base`) are plain in the database, so a copy of it without the key says which profile connects to which configured source. | They copy the admin's vendor setting (`smart_clients`), which is install configuration and plain for the same reason as usernames and profile names: the server looks connections up by it. The patient id, tokens, errors and everything pulled are encrypted. |
| A4 (4.1.3) | A viewer could change the visit summary's questions draft, which was shared by everyone the profile is shared with. | **Fixed (#219):** each person keeps their own draft per profile (`summary_drafts`, encrypted), gone with their access, so no one's questions replace someone else's. Schema step 31 moved each shared draft once: to whoever kept it last and, unless that was a viewer, to the profile's owners and editors too; a viewer never gets someone else's. `tests/test_summary.py` `test_a_draft_per_person`, `SharedDraftMoves`. |
| A5 (8.2.2, 8.2.3) | Offline records (#56), when someone turns them on for a device, are a plain copy of one profile's records in that browser's cache. If the session is ended elsewhere (signed out on another device, account disabled) while the phone stays offline, the copy stays until the sign-in's own end time. | Explained in `www/js/pwa.js` and on the Account page: the key would have to sit beside the copy to open it offline. The copy is deleted on sign-out, on reaching the sign-in page, at the sign-in's end (even offline), on any 401, when the option is turned off, and as soon as a page hears from the server that the profile isn't the person's any more: removed from it, left, or deleted (#591, `SgOffline.still`). Off by default; the Account page says to leave it off on a shared device. |
| A6 (8.2.2) | The lab series picked on Labs, by name, stayed in the browser's `localStorage` after sign-out (as Phase 1 recorded for view preferences). | **Fixed (#220):** on sign-out and on reaching the sign-in or setup page, the picked names (`sel`) are taken out of every saved view (`ht-state:*`, `www/js/pwa.js` `forgetPicks`); the period, tab and kinds stay. browser_check signs out and finds none left. |
| A7 (4.2.2) | `GET /api/profiles/{id}/connect/start?source=...` changes state (a pending sign-in, and a request to the vendor's discovery address) without the `X-Sanguity` check, since a page navigates to it. | The session cookie is `SameSite=Strict`, so a navigation from another site carries no session and is refused. What it does is start a sign-in bound to that session, at a source an admin set up; nothing is changed until the person signs in at the vendor and comes back. |
| A8 (5.3.3) | An HTML record (a portal message, a Health Link's attachment) opened with **Open in new tab** could send that tab elsewhere with a `<meta http-equiv="refresh">`. | **Fixed (#221):** HTML records are served with `sandbox allow-same-origin` added to their CSP (`main.HTML_SANDBOX`), the same sandbox as the app's panel frame, so a refresh is refused in a tab of its own too; PDFs and pictures are served without it, so they still open. `tests/test_api.py` `test_html_record_loads_its_own_pictures_and_styles`, and browser_check opens a record with a refresh in its own tab. |
| A9 (5.2.6, 12.6.1) | An editor who confirms a SMART Health Link makes the server fetch from the host the link names, which can be any public HTTPS host. | That is what a Health Link is. Only after confirming; HTTPS only, public addresses only (loopback, private, link-local and reserved addresses refused, the address checked and then connected to), redirects only on the same host, size and file caps (`config.SHL_*`); each contact is recorded with its host in the profile's activity and the outside-connections view. |
| A10 (12.1.1, 11.1.4) | The watch receiver read an upload's body (up to 32 MB) before checking its signature, with no limit on tries. | **Fixed (#222):** an upload with neither `X-Signature` nor `Authorization: Bearer` is answered 401 at once, and a bearer secret is checked, before any of the body is read; after `BAD_FREE` (10) bad signatures or secrets in 15 minutes an address gets 429 with `Retry-After` for its signed uploads, unread, the wait doubling from 30 seconds up to 15 minutes (`watch/receiver.py` `BadSignatures`, the app's `security.Throttle`). A right bearer secret is known before the body, so it's never held up; a signed upload from a waiting address waits too, since only reading its body could tell it apart. `tests/test_receiver_hae.py` `test_unsigned_refused_before_reading_its_body`, `test_bad_signatures_slow_the_address`. |

## Follow-ups

Not opened as issues; written up here for the owner to file or drop:

1. **A visit summary questions draft per person.** Done in #219: each person's own draft (`summary_drafts`), the shared
   one moved to owners and editors only (and to the viewer who kept it last, if one did), so a viewer's questions never
   replace someone else's (A4).
2. **Clear picked lab names at sign-out.** Done in #220: on sign-out or on reaching the sign-in page, `sel` is removed
   from every `ht-state:*` entry in `localStorage`, the other view preferences kept; none of browser_check's checks
   relied on a selection surviving that (A6).
3. **No refresh in served HTML.** Done in #221: HTML records are served with `sandbox allow-same-origin` added to
   their CSP (HTML only: it would stop PDFs rendering), so a record opened in its own tab can't move it (A8).
4. **The receiver refuses unsigned uploads before reading them.** Done in #222: 401 at once when there's neither an
   `X-Signature` nor an `Authorization: Bearer` header, and an address waits after repeated bad signatures (A10).

## Deployment defaults

- **nginx headers** (`nginx.conf`): the pages are sent with `Cache-Control: no-store`, `X-Robots-Tag: noindex`,
  `nosniff`, `Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`. They also get a CSP with
  `default-src 'self'`, `script-src 'self'`, `object-src 'none'`, `base-uri 'none'` and `frame-ancestors 'none'`.
  `style-src` still allows `'unsafe-inline'`, because the charts set inline styles; that can't run script.
  `server_tokens off` hides the nginx version. Only the pages, `css/`, `js/`, `icons/`, the manifest, the service
  worker, `/connect/return` and `/api/` are served; everything else is a 404. HSTS isn't sent, because nginx speaks
  plain HTTP and HTTPS is your proxy's job (Accepted; the README says how to turn it on at the proxy).
- **No access log** (#203): nginx writes none, in the `compose.yaml` install and the released image alike, and the
  app's own (uvicorn's) is off. The app's security log and each profile's activity say who signed in, what was
  refused and who viewed what.
- **Request sizes:** `/api/` bodies are capped at 64 KB, except one file of an upload (`PUT
  /api/profiles/{id}/uploads/{id}/files`: up to 4 GB at nginx, passed on as it arrives; the app enforces
  `UPLOAD_MAX_FILE_MB`, `UPLOAD_MAX_TOTAL_MB` and `UPLOAD_MAX_FILES`), and the share-link view and calendar feed,
  capped at 1 KB.
- **Routes with no session:** `POST /api/share/view` (only POST is passed on) and the calendar feed (only GET and
  HEAD) have their own `location`s, which drop the `Cookie` header, so neither can be reached with a session.
- **Cookie flags behind a reverse proxy:**
  - The session cookie is HttpOnly, SameSite=Strict and `Path=/`, and gets the `__Host-` prefix over HTTPS.
  - Secure is set when the request came over HTTPS. The app learns that from `X-Forwarded-Proto`, which nginx
    passes on from your proxy, or uses its own scheme. `COOKIE_SECURE=1` forces it on.
  - A client can send `X-Forwarded-Proto` itself when there's no proxy in front. That can only change its own
    cookie, never anyone else's.
  - The device cookie (`sg_device`) only separates one browser's sign-in tries from other people's; it's sent
    only to `/api/login` and lets nobody in.
- **`PUBLIC_ORIGINS` and `X-Forwarded-Host`:**
  - These widen which `Origin` the CSRF check accepts (`main._same_site`), and give the address shown for the app in
    a calendar event, the phone's upload address and a connected source's return address.
  - The check's real strength is the `X-Sanguity` header. A cross-site page can't send it without a CORS
    preflight, and the app never grants one.
  - A client that forges `X-Forwarded-Host` only affects its own requests and answers.
  - Passkeys take the site name from the browser's own `Origin`, and the browser checks that against the
    passkey.
- **Trusting a proxy's client addresses:**
  - nginx replaces `X-Forwarded-For` with the address of the connection itself (`$remote_addr`). uvicorn trusts
    that header from the compose network only, where nginx is the only thing that can reach the app (no published
    port; `FORWARDED_ALLOW_IPS` in `compose.yaml`).
  - Behind your own proxy, every visitor therefore shares the proxy's address. The sign-in throttle allows for
    that: browsers that have signed in before are counted on their own. Since #203, a share link or calendar feed
    that works is never refused because of other people's wrong tokens from that address.
  - `nginx.conf` shows how to trust a specific proxy with `set_real_ip_from`. Trusting a whole range that clients
    can reach would let them pick their own address, and so dodge the throttles.
- **Update path:** `git pull && docker compose pull --ignore-buildable && docker compose build --pull && docker
  compose up -d`, at least monthly (README). Python packages are pinned with hashes, and Dependabot proposes updates
  weekly for them, the images and the GitHub Actions (#77).
- **Containers:**
  - The app, nginx and the watch receiver run as unprivileged users on read-only filesystems, with only `/tmp`
    writable.
  - The app has no published port; the receiver's is `RECEIVER_PORT`.
  - The key file is mounted read-only, apart from the data and the backups.
  - The app sees the watch receiver's folder read-only (#227: its databases and archived uploads go in backups); it
    writes only `keys/` there (the receiver's secrets, and a restore's data for the receiver to take). The receiver
    sees nothing of the app's. TrueNAS's app library mounts the whole dataset writable (deploy/README.md).
  - API docs and OpenAPI are switched off, and uvicorn sends no `Server` header.

## V2 Authentication

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 2.1.1 | Passwords at least 12 characters | Accepted | 15 characters (`PASSWORD_MIN_LENGTH`), or 8 with two-factor sign-in on, per NIST SP 800-63B (`config.PASSWORD_MIN_2FA`). If the second factor goes, a short password must be replaced (`accounts.short_password_check`). |
| 2.1.2 | Passwords of 64+ characters allowed, at most 128 | Met | Up to 256 (`config.PASSWORD_MAX`). |
| 2.1.3 | No truncation | Met | New passwords over 256 are refused, not cut. Sign-in cuts at 256, which no stored password exceeds. |
| 2.1.4 | Any printable Unicode allowed | Met | No character rules (`security.password_problem`). |
| 2.1.5 | Users can change their password | Met | `POST /api/me/password`. |
| 2.1.6 | Change needs the current password | Met | `main.change_password`, throttled. |
| 2.1.7 | Breached passwords refused | Met | About 500,000 breached passwords, offline, also matched without spaces or digits added at the ends (`security.BREACHED`, `_variants`). Passwords built from the username or "Sanguity" are refused too. |
| 2.1.8 | Strength meter | Met | The new-password field says the minimum and suggests a few unrelated words. Refused passwords get a reason. |
| 2.1.9 | No composition rules | Met | Length, breached list and repetition only. |
| 2.1.10 | No forced rotation or password history | Met | None. |
| 2.1.11 | Paste and password managers work | Met | Nothing blocks pasting. Fields have `autocomplete="current-password"`, `new-password` and `one-time-code`. |
| 2.1.12 | Can show the password | Met | Browsers' own reveal controls work. Nothing hides them. |
| 2.2.1 | Anti-automation against guessing | Met; **Fixed (#203)** for share links and feeds | `security.Throttle`: per account and address (5 free tries), per address (20), per account (30), then waits that double up to 15 minutes. Browsers that signed in before are counted on their own, so strangers can't lock an owner out. Two-factor tickets die after 5 wrong codes. At most a few password hashes run at once (`HASH_PARALLEL`). Share-link and feed tokens: wrong ones per address, and every use per link or feed; a working one is no longer refused because of others' wrong ones. |
| 2.2.2 | Weak authenticators (SMS, email) limited | N/A | There are none. |
| 2.2.3 | Notify after authentication changes | N/A | Sanguity has no email or other channel. Changes are in the account activity log for admins (`2fa.on`, `password`, `passkey.add`, ...), and the person sees their own sessions and passkeys. |
| 2.3.1 | One-time passwords random and short-lived | **Fixed** | 3 × 8 random URL-safe characters (`accounts.temp_password`), which must be replaced at first sign-in, and expire after `TEMP_PASSWORD_DAYS`. |
| 2.3.2 | Users can enrol their own authenticators | Met | Authenticator app (`/api/me/2fa/*`) and up to 20 passkeys (`passkeys.PER_ACCOUNT`). |
| 2.3.3 | Renewal of time-bound authenticators | N/A | None are time-bound. |
| 2.4.1 | Passwords stored with an approved slow hash | Met | Argon2id with argon2-cffi's parameters (RFC 9106 low-memory profile), rehashed when they change (`security.needs_rehash`). |
| 2.4.2 | Salt of at least 32 bits | Met | 128-bit random salt per hash (argon2-cffi). |
| 2.4.3 / 2.4.4 | PBKDF2 / bcrypt parameters | N/A | Argon2id is used. |
| 2.4.5 | Extra step with a secret salt (pepper) | Fixed (#74) | Passwords are stored as Argon2id of HMAC-SHA256(pepper, password) (`security.hash_password`). The pepper is 256 random bits, kept in the `meta` table encrypted with the data key (`atrest.load_pepper`), so a copied database can't be checked against guesses without the key file. |
| 2.5.1 | Activation and recovery secrets not sent in clear | Met | One-time passwords are shown once to the admin, never stored in clear and never sent anywhere. |
| 2.5.2 | No password hints or security questions | Met | None. |
| 2.5.3 | Recovery doesn't reveal the password | Met | An admin reset makes a new one-time password. |
| 2.5.4 | No shared or default accounts | Met | The first account is created in the browser with a one-time setup code from the log (about 60 bits, throttled), removed once the account exists (`main._setup_code`). The first-run guide (#199) starts after that, signed in. |
| 2.5.5 | Notify when a factor changes | N/A | No channel, as for 2.2.3. |
| 2.5.6 | Forgotten password recovery is secure | Met | Only an admin, or the command line, can reset. Every session and known browser of the account ends (`accounts.set_temp_password`). |
| 2.5.7 | Lost authenticator needs identity checks | Met | Recovery codes, or an admin's reset (`disable_2fa`), which removes the authenticator app and every passkey and signs the person out everywhere. |
| 2.6.1 | Lookup secrets single-use | Met | `recovery_codes.used_at`. Using one also signs out every other session. |
| 2.6.2 | Lookup secrets of 112 bits, or salted | Fixed (#74) | Recovery codes stay 8 characters, each stored with its own random 128-bit salt as HMAC-SHA256(pepper, salt + code) (`twofactor.recovery_hash`). |
| 2.6.3 | Lookup secrets not guessable | Met | `secrets.choice`, throttled per account. |
| 2.7.x | Out-of-band verifiers | N/A | None. |
| 2.8.1 | Time-based OTPs have a short life | Met | 30-second steps, plus or minus one step for clock drift (`twofactor.DRIFT`). |
| 2.8.2 | OTP secrets protected | Met | Encrypted at rest with AES-GCM (`users.totp_secret`, in `atrest.FIELDS`). |
| 2.8.3 | Approved algorithms | Met | HMAC-SHA-1 TOTP (RFC 6238), the standard for authenticator apps. |
| 2.8.4 | OTP used only once | Met | Steps at or before `totp_last_step` are refused. |
| 2.8.5 | Reuse of an OTP is logged | **Fixed** | Logged as `login.fail` with reason `code already used`. |
| 2.8.6 | OTP devices can be revoked | Met | The person can turn it off (with their password), or an admin can (`disable_2fa`). |
| 2.9.1 | Crypto authenticator keys protected | Met | Passkeys keep the private key on the device. Only the public key is stored. |
| 2.9.2 | Challenge nonce at least 64 bits | Met | py_webauthn's 64-byte random challenge, single use, 5 minutes (`passkeys._Challenges`). |
| 2.9.3 | Approved algorithms | Met | py_webauthn's defaults (ES256, RS256, EdDSA), user verification required. |
| 2.10.1 | No shared secrets between services | Met | The app and nginx share no credential. The app and the watch receiver share each profile's upload secret, sealed with the data key (`watch_keys.publish`), and the key file. |
| 2.10.2 | No default credentials | Met | None. A profile has no upload secret until an owner or editor makes one. |
| 2.10.3 | Service secrets stored securely | Met | The data key is a mode-600 file outside the data, mounted read-only (`compose.yaml`). A connected source's client secret, tokens, refresh tokens and private keys are encrypted at rest (`smart_clients.secret`, `connections.secret`). |
| 2.10.4 | No secrets in source or config | Met | Keys, codes, tokens and upload secrets are generated at install or use time. `.env.example` holds none. |

## V3 Session management

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 3.1.1 | Session token never in a URL | Met | Cookie only. Two-factor tickets, passkey states and share-link tokens go in request bodies; a share link's token is after the `#` of `share.html#…`, which browsers never send. The one token in an address is a calendar feed's, which isn't a session (13.1.3). |
| 3.2.1 | New token at sign-in | Met | `main._start_session` at every sign-in. |
| 3.2.2 | At least 64 bits of entropy | Met | 256 bits (`security.new_token`) for sessions, share links and calendar feeds; 256 bits (`secrets.token_urlsafe(32)`) for a profile's upload secret and a connected source's sign-in state. |
| 3.2.3 | Stored safely in the browser | Met | HttpOnly cookie. Only its SHA-256 is in the database; so are share-link and feed tokens (`share_links.token_hash`, `calendar_feeds.token_hash`) and the sign-in state (`connect_pending.state_hash`). The upload secrets must be compared with an uploaded signature, so they're kept sealed with the data key instead. |
| 3.2.4 | Approved random generator | Met | `secrets`. |
| 3.3.1 | Sign-out ends the session on the server | Met | `POST /api/logout` deletes the session row; a pending connected-source sign-in of that session goes with it (`connect_pending` cascades). |
| 3.3.2 | Re-authentication after 12 hours, or 30 minutes idle | **Changed** (#76; partly Accepted) | 12 hours from sign-in on an account without a second factor, 30 days with one (`main._lifetime`). The idle limit is off by default. |
| 3.3.3 | Option to end other sessions after a password change | Met | They end automatically (`change_password`). |
| 3.3.4 | Users can see and end their sessions | Met | `GET/DELETE /api/sessions`, `revoke-others`, and the Account page. |
| 3.4.1 | Secure attribute | Met | Set over HTTPS (`main._secure`). `COOKIE_SECURE=1` forces it. |
| 3.4.2 | HttpOnly attribute | Met | Session and device cookies (test `test_cookie_flags`). |
| 3.4.3 | SameSite attribute | Met | `Strict`. A connected source's sign-in comes back through a page (`/connect/return`) that posts the code same-origin, since a strict cookie isn't sent on the vendor's redirect. |
| 3.4.4 | `__Host-` prefix | **Fixed** | Over HTTPS, as described above. |
| 3.4.5 | Path set narrowly | Met | The app owns the whole host. The device cookie is limited to `/api/login`. |
| 3.5.1 | OAuth: refresh tokens kept by the relying party | Met | A connected source (#32, #91) is OAuth 2 with PKCE (S256); its refresh token or private key stays on the server, encrypted, and is deleted on disconnect, after revoking it where the vendor allows. The sign-in's state is single use, bound to the session that started it, and lasts 10 minutes (`connections.finish`). |
| 3.5.2 | Static API keys avoided for users | Met | None for people. The phone's upload secret is a device credential for one profile's watch data only, replaced from the Sources page (Accepted A1 on how it travels). |
| 3.5.3 | Stateless tokens signed and checked | N/A | None are relied on: a vendor's access token's `aud` and `iss` are compared with what was asked, and the token itself goes back only to that vendor. SMART Health Cards' signatures are checked against their issuer's keys (`shl.check_card`). |
| 3.7.1 | Re-check before sensitive changes | Met | Changing the password needs the current one. Turning off 2FA and adding a passkey need the password. New recovery codes need a code. Deleting a profile needs its name typed. The most powerful admin actions need a fresh check (4.3.3). |

## V4 Access control

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 4.1.1 | Enforced on a trusted service | Met | Every check is in the API. nginx serves no data (`nginx.conf`, last `location`); pages, the guide and the Admin page hold none and ask the API. |
| 4.1.2 | Access-control data can't be changed by the client | Met | Roles come from `memberships` on the server. The admin flag is changed by admins only, never by yourself (`edit_user`). |
| 4.1.3 | Least privilege | Met; see A2, and A4 (fixed, #219) | Viewer, editor and owner per profile (`main.need`), as in [Who can reach what](#who-can-reach-what). Admin manages accounts and the install but sees no records. A setting's scope decides who changes it (`settings.may_change`). Every route is listed with its check (`tests/test_security_refresh.py`). |
| 4.1.5 | Fails securely | Met | Errors are raised before any data is read. A profile that isn't shared with you is a 404 (`need`). A refused share-link or feed token is the same bare 404 whatever the reason. |
| 4.2.1 | No insecure direct object references | Met | Every profile route calls `need()`. What's inside a profile (uploads, jobs, imports, edits, journal entries, cost marks, "same result" decisions, connections, share links, calendar feeds, documents) is looked up with its profile's id, so another profile's id finds nothing. Sessions and passkeys are matched to the caller's `user_id`; a feed only by its owner, or by an owner of its profile. A sign-in coming back from a vendor is checked against the profile and session it was started from. |
| 4.2.2 | CSRF protection | Met; see A7 | `X-Sanguity` header plus Origin check on every state-changing `/api/` call (`main.guard`), and SameSite=Strict (`test_csrf`). |
| 4.3.1 | Admin interfaces use multi-factor | Met (#75) | The `admin` dependency refuses every admin route, the Admin page's backups, vendor setup, lab-name matches and install settings included, until the admin has an authenticator app or a passkey, once the server has more than one account. |
| 4.3.2 | No directory browsing or metadata files | Met | nginx serves an allow-list of paths, with no autoindex. |
| 4.3.3 | Step-up for lower-value systems | Met (#75) | Resetting a password, turning off someone's two-factor sign-in, making someone an admin, and turning scheduled backups off or keeping fewer (#200) (`main.fresh`) need the password, a code or a passkey within `REAUTH_MINUTES` (5). |

## V5 Validation, sanitization and encoding

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 5.1.1 | HTTP parameter pollution | Met | FastAPI and pydantic take one typed value per parameter. |
| 5.1.2 | Mass assignment | Met | Each route has its own pydantic model with only the fields it accepts; the one route taking a free object (`PATCH .../connections/{id}`) refuses any key but `kinds`, `since` and `attach`. |
| 5.1.3 | Input validated by allow-list | Met | Roles, schedules, feed details and similar are `Literal`s; settings each have a checker (`settings.define`); share-link sections, dates and labs are checked (`share_links.clean`); upload paths are normalised and refused if absolute or with `..` (`uploads.clean_path`); time zones match a pattern. nginx caps request bodies (see [Deployment defaults](#deployment-defaults)). |
| 5.1.4 | Structured data typed | Met | pydantic. |
| 5.1.5 | Redirects only to allowed places | Met | No redirect takes a URL parameter. A connected source's sign-in redirects to the vendor's authorization address from the discovery an admin tested, and back to the Sources page. |
| 5.2.1–5.2.3 | Rich text, unstructured input, SMTP | N/A | Journal entries, questions and notes are plain text, shown with `textContent`. No email. |
| 5.2.4 | No eval of user data | Met | None in the server or the pages; the CSP forbids inline script and eval. Milestone rules are regular expressions, length-limited and checked for catastrophic backtracking (`tests/test_milestones.py`). |
| 5.2.5 | Template injection | N/A | No server-side templates. The PDF is drawn by `pdfdoc.py` from text. |
| 5.2.6 | SSRF | Met (was N/A); see A9 | The server's only way out is `server/net.py`, with a permit per purpose naming the hosts allowed (`outside.py`): a connected source's hosts as an admin set it up and tested it, or a Health Link's host after an editor confirmed it. Any other host is refused, and a disconnected source's hosts at once, even mid-pull. Health Links: HTTPS only, public addresses only, the checked address connected to, same-host redirects only. `tests/test_outside.py` checks that nothing in `server/` opens a connection any other way. |
| 5.2.7 | SVG scriptable content | Met | Uploaded SVG isn't served (not in `DOC_TYPES` or `PART_TYPES`). |
| 5.2.8 | Template languages | N/A | None. |
| 5.3.1 | Output encoded for its context | Met | The pages build the DOM with `textContent` and attributes (`el()` in `www/js/util.js`); no `innerHTML` in `www/js`. The calendar feed escapes and folds each value (RFC 5545, `calendar.escape`). The PDF writes text, not markup. |
| 5.3.3 | Protection against reflected, stored and DOM XSS | Met; A8 fixed (#221) | As 5.3.1. Records are shown as text. Documents run under `script-src 'none'` (`main.DOC_CSP`), and an HTML record is sandboxed, in the app's panel and in a tab of its own (`main.HTML_SANDBOX`). A provider's logo is shown only if it's a `data:` image. |
| 5.3.4 | Parameterized database queries | Met | Every value is a `?` parameter. The f-strings in SQL hold fixed table and column names only. |
| 5.3.6–5.3.7, 5.3.10 | JSON, LDAP and XPath injection | N/A | None. |
| 5.3.8 | OS command injection | Met | The app runs no commands. |
| 5.3.9 | Local file inclusion | Met | Document paths are fixed at import and checked to be inside the profile's or exports folder (`profiles.resolve_document`, `_inside` with `realpath`). A backup is named, never given as a path (`backup.NAME`). |
| 5.5.1–5.5.3 | Deserialization | Met | JSON only; no pickle or YAML. XML importers refuse a document that declares a `DOCTYPE` or `ENTITY` before parsing (C-CDA, CareSpace), or any `<!ENTITY` (Apple Health, whose own DOCTYPE only declares elements); Python's expat doesn't load external entities, and the image's is current (Alpine's, with its protection against entity expansion). A backup's tar is checked member by member, regular files with safe names only, against its manifest, before anything is read (`backup.verify`). |

## V6 Stored cryptography

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 6.1.1–6.1.2 | Private and health data encrypted at rest | Met; see A3 | AES-256-GCM for every file Sanguity writes and every sensitive database field (`crypto.py`, `atrest.FIELDS`). Since Phase 1 that includes uploads and their file names, previews, import names, corrections, typed-in records, settings, the journal, cost marks, "same result" decisions, share links' names and scope, the questions draft, outside contacts' hosts, a connection's tokens, patient id, errors and attachments, and each profile's upload secret. `tests/test_security_refresh.py` fails on any new text column that is neither encrypted nor listed as plain with why. Plain on purpose: usernames and profile names, ids, hashes, states and times, install configuration (connected-source apps, backup runs), your own original exports (read-only) and downloads from "Export everything". |
| 6.1.3 | Financial data encrypted | Met | Claims, payments and cost marks are encrypted with the records. |
| 6.2.1 | Fails securely | Met | A failed GCM tag check raises an error, and nothing is returned. Start-up stops with a reason if the key is missing or wrong (`atrest.check_key`). |
| 6.2.2 | Proven primitives | Met | AES-GCM from `cryptography` (OpenSSL); Python's `secrets`, `hashlib` and `hmac`. |
| 6.2.3 | Safe modes, IVs and padding | Met | GCM with a random 96-bit nonce for each encryption, and a 128-bit tag. |
| 6.2.4 | Algorithms can be replaced | Met | Formats carry a version marker (`SGE1`, `enc:1:`) and a key id. Keys rotate with `setup new-key` and re-encryption at start-up. |
| 6.2.5 | No weak algorithms | Met | SHA-1 only inside HMAC for TOTP, as RFC 6238 specifies. |
| 6.2.6 | Nonces unique | Met | 96 random bits each time. |
| 6.2.7 | Encrypted data authenticated | Met | GCM. |
| 6.2.8 | Constant-time comparisons | Met | `hmac.compare_digest` for the setup code, TOTP and the receiver's signatures and secrets (every profile's compared, so the time doesn't say which matched). Tokens are looked up by their hash. |
| 6.3.1 | Approved random generator | Met | `secrets` everywhere. |
| 6.3.2 | Random GUIDs | N/A | None. A calendar event's UID is a keyed hash that says nothing (`calendar._uid`). |
| 6.4.1 | Key management | Met | Key file outside the data and the backups, mode 600, rotation documented (`crypto.py`, README). The Admin page's Backups shows where it is, as a reminder to keep a copy elsewhere. |
| 6.4.2 | Key kept in an isolated module | Accepted | The key is a file held in the app's memory. The README shows how to keep it in a Docker/Podman secret or a systemd credential instead. |

## V7 Errors and logging

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 7.1.1 | No credentials or session tokens in logs | Met; **Fixed (#203)** for nginx | uvicorn's access log is off, and since #203 so is nginx's, whose lines held a connected source's sign-in code. The security log leaves out passwords, codes, tokens and usernames that match no account (`test_security_log`). Share-link and feed tokens, upload secrets, Health Link keys and passcodes, and a source's tokens are never logged; outside contacts are logged by host only. |
| 7.1.2 | No other sensitive data in logs | **Fixed (#203)** | Before: nginx logged uploaded file names, provider searches and documents' file names, and a failed import or pull logged its exception's message, which can name a file or quote a record. Now: no access log, and exceptions are logged by type and place only (`main.QuietTracebacks`). Start-up and build messages name profiles by number. |
| 7.1.3 | Security events logged | **Fixed** | `main._audit` (see [Fixes made](#fixes-made)). Since Phase 1 also `share.refused`. |
| 7.1.4 | Enough detail to investigate | **Fixed** | Each line has the time, event, address, account and reason. |
| 7.2.1 | Authentication decisions logged | **Fixed** | `login.ok` (with method), `login.fail` (with step and reason), `login.throttled`, `setup.fail` and `reauth.fail`. |
| 7.2.2 | Access-control failures logged | **Fixed** | `access.denied` (profile, role needed) and `csrf.refused`. |
| 7.3.1 | Log injection prevented | Met | Each event is one JSON-encoded line. Activity details are stored as JSON and shown with `textContent`. |
| 7.3.3 | Logs protected | Met | Docker's own log storage on the host. A profile's activity log is readable only by its owners, and is in Export everything only when an owner exports (#589); account events, backups, vendor setup and outside contacts with no profile, by admins. |
| 7.3.4 | Time synced | N/A | The host's clock. |
| 7.4.1 | Generic error message | Met | Unhandled errors return a plain "Internal Server Error". Debug mode and tracebacks are never sent. A failed import tells the person why in plain words, stored encrypted. |
| 7.4.2 | Exception handling | Met | Known failures map to clear 4xx answers. A failed import saves nothing (`tests/test_jobs.py`); a failed pull backs off and says so; a failed backup is shown on the Admin page. |
| 7.4.3 | Last-resort handler | Met | Starlette's, logged quietly (7.1.2). |

What is in each profile's activity log, readable by its owners: views and document opens (once per session), every
change (uploads, imports and their undo, corrections, typed-in records, journal entries, cost marks, settings,
names), exports, visit summary PDFs made (sections and dates, never the questions or a result), sharing changes,
share links made, revoked and every view of one (by "share link", with the viewer's address, never the token),
calendar feeds made, rotated, changed and revoked (fetches only as "last fetched"), the upload secret made or
replaced, connected sources connected, changed, disconnected and stopping, and each outside contact. Account events,
readable by admins: account changes, require-two-factor, lab-name matches, install settings, vendor setup, backups
made, failed, checked and the schedule changed.

## V8 Data protection

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 8.1.1 | Sensitive data kept out of caches | Met | `Cache-Control: no-store` on pages and every API answer (checked for each route in `Reach.test_the_access_table`), PDFs and exports included. A calendar feed is `private, max-age=0`: the calendar app may keep it, never a shared cache. The service worker never handles `/api/` (`www/sw.js`). |
| 8.1.2 | Cached copies on the server purged | N/A | No server cache. An upload's decrypted copy for an import is in a private temporary folder removed when the import step ends (`uploads.open_plain`); the PDF is made in memory. |
| 8.1.3 | Few parameters, no sensitive ones in URLs | Met; see 13.1.3 | Sensitive values go in request bodies. An upload's file name is a query parameter (the body is the file), no longer logged by nginx (#203). |
| 8.1.4 | Abnormal request volume detected | Fixed (#78) | Failed sign-ins are throttled, logged and kept 30 days (`alerts.py`); admins see a notice on the Admin page. Share links and feeds are throttled per link or feed. Other endpoints need a signed-in session and aren't rate-limited (Accepted). |
| 8.1.5 | Backups made | Met | `server.manage backup`, or **Admin → Backups** (Back up now, or a daily or weekly schedule, #200): a consistent SQLite copy plus files, with a manifest of SHA-256 hashes and row counts, and the watch receiver's databases (each a whole snapshot it replaces at once) and archived uploads (#227), counted too. **Check a backup** restores one into a temporary folder in the backups folder, compares it and deletes it, leaving the live data as it was. Admins only (with their second factor); turning the schedule off or keeping fewer needs the fresh check (4.3.3); no endpoint downloads or restores a backup. |
| 8.1.6 | Backups stored securely | Met | The data in them is encrypted, and the key isn't included. |
| 8.2.1 | Anti-caching headers | Met | As 8.1.1. |
| 8.2.2 | Nothing sensitive in browser storage | Met; see A5, and A6 (fixed, #220) | `localStorage` holds view preferences only: theme, period, tab, which lab series are picked (until sign-out, #220), the profile last opened. `sessionStorage` holds an entry's id across a reload. The one copy of records a device may keep is offline records (#56), off unless turned on per device. |
| 8.2.3 | Browser data cleared at sign-out | Met; see A5 | Session cookies are deleted; the offline copy is deleted at sign-out, on reaching the sign-in page, on a 401 and at the sign-in's end. A sign-out made without the server (offline) deletes the copy at once and ends the session once the server is reached; until then every page opens on the sign-in page, which says so (#422), and every tab already open goes there at once (a storage event), so none keeps the session alive; what the sign-in left waiting (a theme picked offline for the account) is deleted with the copy, so it never reaches the next account signed in on the device (#429), and so is the account's theme as the browser keeps it for the first frame (#433). Log out asks every open tab first (a BroadcastChannel question each answers from its own "Leave site?" guards), names those with changes not saved, and once it goes ahead sends them all to the sign-in page, online or not (#432). |
| 8.3.1 | Sensitive data in bodies or headers, not URLs | Met | As 8.1.3. |
| 8.3.2 | Users can export and delete their data | Met | Export everything (owners and editors), delete a profile (owners), remove an account (admins), leave a profile. Deleting a profile removes its uploads, documents, journal, share links, calendar feeds, connections and upload secret, and the watch receiver deletes what it kept for the profile (#588); no id of a deleted row is given to another (schema version 39). |
| 8.3.3 | Clear language on what's collected | N/A | Self-hosted: the install's owner holds the data. The README says what's stored and where; the Sharing page says plainly what each share-link section and calendar feed setting shows. |
| 8.3.4 | Sensitive data identified | Met | `atrest.FIELDS` and the plain list in `tests/test_security_refresh.py`, `crypto.py`, `activity.py`, `backup.py`, and [database.md](database.md#what-is-encrypted). |
| 8.3.5 | Access to sensitive data audited | Met | Views and document opens are logged once per session per profile (a session's id is never another's, and the account must match too, #588), share-link views every time, PDFs and exports each time, readable by owners (`activity.once`, `activity.log`). |
| 8.3.6 | Sensitive data cleared from memory | N/A | Python offers no reliable way. |
| 8.3.7 | Sensitive data encrypted | Met | As V6. |
| 8.3.8 | Retention | Met | The activity log keeps 365 days (`ACTIVITY_DAYS`); an ended share link stays listed for its owners 30 days, and is deleted when a link is next made. An upload (a sealed copy of the files sent) is kept while it waits in review or an import holds it, until that import is undone or the profile deleted; one cancelled, failed, interrupted or saving nothing new goes with it (`jobs.py`, #547), and its job keeps only how many records it held, never the samples, lab names or matches its review showed (#591, `jobs.counts_only`), and any left over (still being sent after a day, or read by a job and now held by no job, import or document) at the next start (`uploads.cleanup`). Records are kept until their owner deletes them. |

## V9 Communications

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 9.1.1 | TLS for all client connectivity | Accepted; see A1 | HTTPS is the owner's reverse proxy or Tailscale, and passkeys, offline records and installing the app need it. The sign-in and Account pages warn when opened over plain HTTP from outside the home network or a tailnet (`plainHttpWarning` in `www/js/util.js`). |
| 9.1.2–9.1.3 | Strong TLS settings | N/A | Sanguity terminates no TLS. |
| 9.2.1 | Outbound connections use trusted TLS | Met (was Accepted) | Connected sources and Health Links are HTTPS with the certificate checked against the system's trust store for the host; Health Links require TLS 1.2 or later. `http://` only with `CONNECT_ALLOW_HTTP`, for a local test server. nginx to the app stays on the compose network (Accepted). |
| 9.2.2 | Encrypted connections to other services | Met; nginx to the app Accepted | As 9.2.1. |

## V11 Business logic

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 11.1.2 | Business limits | Met | At most 20 working share links per profile, 90 days each; one calendar feed per person and profile; one backup or check at a time; uploads, Health Links and pulls have size and count caps (`config.UPLOAD_*`, `SHL_*`, `SYNC_*`). |
| 11.1.4 | Anti-automation | Met; **Fixed (#203)**; A10 fixed (#222) | As 2.2.1 for sign-in, share links and feeds; a pull backs off after failures and when the vendor says to wait; the watch receiver makes an address wait after 10 bad signatures (#222). |

## V12 Files and resources

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 12.1.1 | Large files refused | Met (was: no uploads) | One file at most `UPLOAD_MAX_FILE_MB` (4 GB), an upload at most `UPLOAD_MAX_TOTAL_MB` (16 GB) and `UPLOAD_MAX_FILES` (20,000), counted as it's written and refused before any of it is read when the size is announced (`uploads.add_file`, `tests/test_uploads.py`). Health Links: `SHL_MAX_MB` and `SHL_MAX_FILES`. A connected source's attachments: `SYNC_MAX_ATTACHMENT_MB` and a budget per connection. |
| 12.1.2 | Compressed files checked | **Fixed** | A zip uploaded is unpacked on the server with symbolic links, absolute or `..` names and encrypted entries refused, the file and byte limits counted after unpacking, and any entry that unpacks to more than `UPLOAD_MAX_ZIP_RATIO` times its size refused as a zip bomb, counting what is written rather than what its header says (`uploads._unzip`). Zips inside an export are read with their own caps (`epic_mychart._cda_files`). `server.manage import` has its own limits (Phase 1). |
| 12.1.3 | Per-user quota | Accepted | Limits are per upload; an editor can make more uploads. A household's own server, whose editors are trusted with the records; abandoned uploads are removed after a day, and an upload whose import failed, was cancelled or kept nothing is removed with it (#547). |
| 12.2.1 | File type checked | Met | Importers recognise files by their content, and an upload that no importer reads saves nothing. Documents are served from a fixed table of types; anything else downloads as `application/octet-stream`. |
| 12.3.1 | Submitted file names not used directly | Met (was: no uploads) | Uploaded files are stored under random names; the name sent is kept encrypted and normalised by `uploads.clean_path`. Documents are reached by random id. |
| 12.3.2 | No path traversal | Met | `uploads.clean_path` (no absolute paths, `..`, drive letters, backslashes or control characters), the decrypted copy checked to be inside its folder (`uploads.open_plain`), and `_inside` with `realpath` for documents (tests: `test_document_paths`, `tests/test_uploads.py`). |
| 12.3.3 | No remote file inclusion | Met | Nothing fetched is ever run or included; what a source or Health Link returns is parsed as data. |
| 12.3.4 | No reflected file download | Met | `Content-Disposition` carries the quoted original name. API answers are `attachment` (14.4.2). |
| 12.3.5–12.3.6 | OS commands and file paths | N/A | None. |
| 12.4.1 | Files stored outside the web root | Met | nginx serves only `www/`. Records, uploads and backups are reached through the API, or not at all. |
| 12.4.2 | Antivirus scan | Accepted | Uploads are your own exports, stored encrypted, parsed as data and never run; documents are served under `script-src 'none'` with `nosniff`. A virus scanner is unusual on a home server; your devices' own protection applies when a document is downloaded. |
| 12.5.1 | Only allowed file types served | Met | nginx allow-list. The API's `DOC_TYPES` and `PART_TYPES`. |
| 12.5.2 | Uploaded files never run | Met | Documents are served under `script-src 'none'`, with `nosniff`. |
| 12.6.1 | SSRF allow-list | Met (was N/A); see A9 | `net.Permit` (5.2.6). The hosts a connected source may contact are those of the discovery an admin tested; an admin can point a source at a local FHIR test server, nobody else can add one. |

## V13 API and web service

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 13.1.1 | Same encoding throughout | Met | JSON over UTF-8; iCalendar and PDF for the two downloads that are those. |
| 13.1.3 | No sensitive data in API URLs | Met; calendar feeds Accepted | Ids, a search and an upload's file name only, none of them logged (#203). A calendar feed's token is in its address because a calendar app can't sign in: it opens only that person's feed of that profile, minimal by default, is stored as a hash, can be rotated or revoked at once, stops when the person loses access, and is never logged by nginx or the app. |
| 13.1.4 | Authorization at both route and resource level | Met | Dependencies (`user`, `admin`), then `need()` per profile, then the profile's id on every lookup inside it. |
| 13.1.5 | Unexpected content types refused | Met | FastAPI accepts only JSON bodies for its models. An upload's body is the raw file, read only by the upload route. |
| 13.2.1 | Only intended HTTP methods | Met | Others get 405; nginx passes only POST to the share-link view and GET/HEAD to the feed. |
| 13.2.2 | JSON schema validation | Met | pydantic models. |
| 13.2.3 | CSRF protection | Met | As 4.2.2. |
| 13.2.5 | Content type checked | Met | As 13.1.5. |
| 13.2.6 | Message integrity | Met | The receiver checks each Health Connect upload's HMAC-SHA256 signature (A1 for Health Auto Export); Health Cards' signatures are checked. |
| 13.3.x, 13.4.x | SOAP, GraphQL | N/A | None. |

## V14 Configuration

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 14.1.1 | Repeatable builds | Met | Dockerfile, hash-pinned requirements, CI builds and runs the stack (`.github/workflows/check.yml`). |
| 14.1.3 | Hardened server configuration | Met | Unprivileged users, read-only filesystems, no published app port, `server_tokens off`, no access log. |
| 14.1.4 | Redeploy is automated | Met | `docker compose up -d --build`. |
| 14.2.1 | Components up to date | Met | Dependabot: weekly proposals for the Python locks, the images and the GitHub Actions. The README's update command pulls newer images and base images (#77). |
| 14.2.2 | Unneeded features off | Met | No API docs or OpenAPI. Static serving only in development (`SANGUITY_SERVE_STATIC`). Outside contact only for what someone connected. |
| 14.2.3 | Subresource integrity for outside assets | N/A | Nothing is loaded from elsewhere (`default-src 'self'`): no fonts, maps or logos from other sites. |
| 14.2.4 | Components from trusted sources | Met | PyPI with `--require-hashes`. Official Python and nginx images. The provider directory is a bundled file, built from vendors' published lists (`tools/build_directory.py`), never fetched by the app. |
| 14.2.5 | Inventory of components | Met | `requirements.txt` (with hashes) and the image tags. |
| 14.2.6 | Third-party components isolated | Met | Separate containers. The app has no published port and only nginx can reach it. |
| 14.3.1 | Generic server errors | Met | As 7.4.1. |
| 14.3.2 | Debug modes off | Met | Nothing enables them. |
| 14.3.3 | No version headers | Met | `server_tokens off`. uvicorn `server_header=False`. |
| 14.4.1 | Content-Type with charset | Met | JSON, iCalendar and documents' text types carry `charset=utf-8`. |
| 14.4.2 | API answers carry `Content-Disposition: attachment` | **Fixed** | `main.guard`. |
| 14.4.3 | CSP | **Fixed** | No inline script, on every page including the share, guide, Sources, Sharing and Admin pages. See [Deployment defaults](#deployment-defaults). |
| 14.4.4 | `nosniff` | Met | nginx and the API. |
| 14.4.5 | HSTS | Accepted | Your HTTPS proxy's job; the README gives the header to set there. |
| 14.4.6 | Referrer-Policy | Met | `no-referrer`, so a share link's or a connected source's return address never leaves in a `Referer`. |
| 14.4.7 | Framing controlled | Met | Pages: `frame-ancestors 'none'` and `DENY`. Documents: framed by the app only (`SAMEORIGIN`, `frame-ancestors 'self'`). |
| 14.5.1 | Only needed methods | Met | As 13.2.1. |
| 14.5.2 | Origin not the only access control | Met | Origin is checked in addition to the custom header and the session. |
| 14.5.3 | CORS allow-list | Met | No CORS headers at all. |
| 14.5.4 | Proxy headers only from trusted proxies | Met | See [Deployment defaults](#deployment-defaults). |

## Features since Phase 1

What each added, and how it keeps to the rules above. Each file named has its own tests (CONTRIBUTING lists them).

### Uploads, imports and their history (#19, #20, #24, #38)
- Owners and editors only. Each file is streamed to disk sealed (`crypto.StreamWriter`) under a random name, its
  path kept encrypted; a zip is unpacked on the server under the limits in 12.1 (`server/uploads.py`).
- An import runs in the background on a decrypted copy in a private temporary folder, removed when it ends; a
  failed one saves nothing and tells the person why, encrypted, and is logged by type and place only (#203).
- Undoing an import puts everything back exactly; its raw files stay with the profile, in exports and backups.

### Typed-in records, corrections, "same result" and lab-name matches (#27, #33, #96, #23)
- Owners and editors; each change is in the activity log and encrypted. Lab-name matches are install-wide, so only
  admins change or remove one, and they show no result or profile (A2). Deleting a profile forgets those its own
  reviews made, unless another profile's import has been saved with one since or an admin has kept or changed it
  (#591, `labmap.forget_profile`); the delete dialog says so.
- The entries offered with a rename or a change of category (`GET .../edits/similar`, #237) are owners' and editors'
  only, like the corrections themselves: the profile's own entries, by id, as its records already show them. A batch
  is checked against that list when it's saved, so it can only reach the profile's entries that look the same, at
  most 2,000 at once; a category rule moves only entries between the free-form kinds (`edits.MOVABLE`).

### Settings (#35, #36-#45)
- A setting's scope says who changes it: a profile's, its owners and editors; a person's own, that person; the
  install's, admins with their second factor. Values are never logged, only which setting changed. Private ones are
  sealed; the plain ones are listed in `tests/test_security_refresh.py`. Milestone regular expressions are checked.

### Connected sources and their sync controls (#32, #91, #92, #99, #101, #102, #165)
- Each install registers its own app with each vendor; admins set up and test it on the Admin page, its client
  secret encrypted. "Waiting for the vendor's review" (#165) only stops anyone connecting it meanwhile.
- Owners and editors connect a profile: OAuth 2 with PKCE, a 256-bit state stored as a hash, single use, bound to
  the session and the profile, 10 minutes. The code is exchanged only at the token endpoint discovery named when it
  was tested, and the token's audience and issuer are checked. Tokens, refresh tokens and dynamic clients' private
  keys are encrypted; disconnecting revokes where the vendor allows and deletes them.
- Choosing what's pulled, fetching a left-out attachment, removing a kind's records and Sync now are editors'; the
  sync's warnings show to viewers as a quieter note. Every contact goes through `net.py` (5.2.6).

### Outside connections (#59, #97)
- `GET /api/outside` lists every host the install may contact and when it did: admins the whole install, owners and
  editors their own profiles, viewers nothing. Hosts are encrypted at rest; never a path, query or token.

### SMART Health Links and Cards (#94)
- Pasting a link only reads what it says; fetching needs the editor's confirmation. The key and passcode are never
  stored or logged. HTTPS, public hosts only, caps on size and files; Health Cards' signatures checked against their
  issuer's keys (A9).

### The provider directory (#93)
- A bundled file searched on the server; no outside lookups, logos shown only as `data:` images.

### Calendar feeds (#51)
- One per person and profile, for anyone who can view it; minimal by default ("Appointment", "Labs expected"), never
  results, diagnoses or procedures. 256-bit token stored as a hash, shown once, rotated or revoked at once; works only
  while its person can view the profile with an active account. Owners see and revoke everyone's. Throttled per feed;
  wrong tokens per address, without shutting out a working feed (#203). Not logged by nginx or the app (13.1.3).

### The journal, medications and costs (#49, #50, #55)
- Read by anyone the profile is shared with; written by owners and editors; every change logged without its text;
  bodies and cost marks encrypted; in exports and backups.
- Photos (#161, `server/journal_photos.py`): accepted only as JPEG, PNG or WebP, by the file's own bytes and a walk of
  its structure (a file that isn't a whole picture of its type is refused; SVG, GIF, HEIC and anything else too), at
  most `JOURNAL_PHOTO_MAX_MB` (20; nginx caps the one route at 25 MB), six to an entry, thirty waiting per profile
  (abandoned ones swept after a day: whenever a photo is sent or an entry saved, and when the app starts, with any file
  no photo names, #591; a photo's file is removed only once the change that drops it is committed). Metadata is taken out before anything is stored, without decoding the picture:
  in JPEG every EXIF block (GPS, camera make, model and serial, times, thumbnail, maker notes), XMP, IPTC/Photoshop,
  comments, other application blocks and anything after the picture's end; kept: JFIF, the ICC colour profile,
  Adobe's colour flag, and the orientation, written back as an EXIF block holding nothing else. In PNG, text chunks,
  `eXIf` and `tIME`; in WebP, the EXIF and XMP chunks. Sealed in `profiles/<id>/journal/` under random names; served
  inline under the documents' strict CSP with `nosniff`, only while an entry lists the photo. The symptom groups are
  the sealed profile setting `journal.groups`.
- Medication reminders (#162, `med_reminders`): the same, the body (medication, times, days, dates, zone) encrypted,
  the activity log saying only what was done and how many times a day. A calendar feed lists them only while they are
  on, minimal ("Medication") unless its person chose full details; the feed's own rules (above) are unchanged.
- The medication log (#235, `med_doses`): the same again; the activity log keeps the dose's status, never the
  medication or the note. A phone's doses arrive with the watch data (Health Auto Export, sealed like the rest of it).

### About this person (#194)
- Birth date, sex used for reference ranges and height history are three profile settings (`person.*`), sealed like
  every health setting; no new column. Read by anyone the profile is shared with (`GET …/person`, viewer); changed by
  owners and editors (`PUT …/person`, `POST …/person/heights`, `DELETE …/person/heights/{day}`), each change logged as
  which setting changed, never its value. In exports and backups with the other settings. A share link shows them
  only when its owner ticks "About this person", which is unticked by default; nothing else reaches them. The due
  date for a baby born early (#196, `person.dueDate`) is one more such setting, changed with the birth date by the same
  `PUT`, and not in share links. So are targets of the person's own for home readings (#234, `person.targets`),
  changed per measure by the same `PUT` (owners and editors); a visit summary or share link includes them only when its
  "Your targets for home readings" section is ticked, unticked by default. The growth charts' tables (`www/js/growth-lms.json`, #196) are published reference
  data with no one's health in them, fetched from the app's own files like a script.

### The visit summary PDF (#48, #213)
- Anyone who can view the profile; made in memory, never written to disk, `no-store`; every PDF logged without its
  questions or results. The questions draft is each person's own (#219) and encrypted (A4).
- The phone's and watch's measures, immunizations, ECGs and BMI (#225, `server/measures.py`) are sections of their own,
  offered only when the profile has some and unticked unless the person ticks them; the same for share links, built
  from named fields only (no samples, no raw summary). No new route or column.

### Share links (#47)
- **Token (3.2.2, 6.3.1, 3.1.1, 8.1.3):** 256 random bits (`security.new_token`), shown once; only its SHA-256 is
  stored. It sits after the `#` of `share.html#…`, which browsers never send, and the page posts it in the request
  body, so it's in no URL, server log or `Referer`. The security log never holds it.
- **Least privilege (4.1.3, 4.2.1):** only owners make, list and revoke links. A link shows only the sections, labs
  and dates chosen, built on the server from named fields; no documents, report text, settings or other profile, and
  no route that changes anything. It works only until it expires (at most 90 days) or is revoked, and only while its
  maker is still an owner with an active account, checked at every view.
- **Fails securely (4.1.5, 7.4.1):** a wrong, malformed, expired or revoked token, and a body that isn't JSON, all get
  the same bare 404.
- **Anti-automation (2.2.1, 8.1.4):** `security.Throttle` per address for wrong tokens and per link for every view; a
  working link opens whatever others' wrong tokens from its address (#203).
- **Sessions (3.4):** the view reads no cookie and sets none; nginx strips cookies before it.
- **Logging (7.1.1, 8.3.5):** every view is in the profile's activity, readable by its owners, with the viewer's
  address and never the token; refused tokens are a `share.refused` security event.
- **Pages (14.4.3):** `share.html` is served under the same CSP as the other pages, with no inline script.

### Phone and watch uploads per profile (#46, #148, #166)
- Each profile's own secret, made or replaced by its owners and editors on the Sources page, shown once and then
  only as its last four characters; kept sealed in the settings table and in the receiver's sealed secrets file.
  Health Connect signs each upload with it; Health Auto Export sends it as a bearer token (A1). Every profile's
  secret is compared in constant time; an upload lands only in the profile whose secret matched.

### Menstrual cycle tracking, opt-in (#183)
- Off for every profile until one of its owners turns it on (`PUT /api/profiles/{pid}/cycle`, owners only; reading it
  is owners' and editors', viewers get 403 and their page never asks). While off, the receiver takes every cycle
  record out of an upload before archiving it (`cycle.keep`), so none reaches `raw/`, its database or any summary;
  sexual activity is taken out of every upload, on or off, and never kept. A secrets file it can't read counts as
  off for keeping and deletes nothing (`receiver.cycle_state`). At its first start after this change it rewrites
  the uploads it archived before without them, once.
- Kept, they're in the receiver's sealed database and a sealed `cycle.json` apart from `daily.json`, so nothing that
  reads the watch summary (share links, the export's `watch/daily.json`, the visit summary) carries them by accident.
  `sanguity.db` holds only the sealed setting, never a record; the offline copy (#56) and `localStorage` never hold
  them. Never in a share link; in Export everything (`?cycle=1`) and the visit summary only when ticked each time, and
  the activity log says so (never a record).
- Delete all cycle data and turning it off hide them at once and ask the receiver, through the secrets file, to empty
  its table (then `VACUUM`, so no free page keeps them) and rewrite every archived upload without them; `cycle.json`
  says which deletion was done. What can't be reached is said on the card: backups made before (they can't be
  rewritten), files already downloaded, an uploaded Apple Health export (kept as uploaded; its cycle records are read only while tracking is on,
  #223, kept sealed as the import's own in `watch/cycle/<import id>.json`, never in a summary, and deleted with the
  rest at once by the app, waiting previews included), and the phone. Tests: `tests/test_cycle.py`.

### Offline records and installing the app (#56)
- The service worker keeps only the app's own pages, styles, scripts and icons, never `/api/`, documents or the
  connected-source return page. Offline records are a separate, opt-in copy (A5).

### The Sources, Sharing and Admin pages, and the first-run guide (#166, #168, #167, #175, #199)
- Pages only: they hold no data and show what the API lets the person see, by role. The Admin page's sections are
  each behind the `admin` dependency. The guide's state is a per-person setting ("", "skipped", "done") in plain
  text; it names no record.

### Backups in Admin (#200)
- Admins only, with their second factor; turning the schedule off or keeping fewer needs a fresh check. A check is
  by backup name, never a path, and restores into a temporary folder that's deleted; nothing downloads or restores a
  backup over the live data (that stays a command-line step with the app stopped). `backup_runs` holds file names,
  sizes, states and counts only. Every backup, failure, check and schedule change is an account event.
- The phone and watch data (#227) goes in as the receiver sealed it, never decrypted on the way; a check opens each
  database in memory only. Cycle records (#183) are compared but never named on the page or in a check's result: only a
  profile's owners and editors may know they're kept. A restore writes the receiver's data to its `keys/restore/`,
  which the receiver takes at start, keeping what it had aside; nothing in the API reaches either.

### Big records stay fast (#201) and the anonymiser (#34)
- Performance budgets change no access or storage. The anonymiser is out of scope (see the top of this document).
