# Security review: Phase 1 against OWASP ASVS

The Phase 1 review from issue #8. It covers accounts, profiles and roles, sessions, two-factor sign-in, passkeys,
the activity log, encryption at rest, backups and export, and the default Docker install.

- **Standard:** OWASP ASVS 4.0.3, Level 2. The chapters Phase 1 touches: V2 authentication, V3 sessions, V4
  access control, V5 validation and encoding, V6 stored cryptography, V7 errors and logging, V8 data protection,
  V9 communications, V12 files, V13 API and V14 configuration. Level 3-only requirements are left out.
- **Code reviewed:** `main` after #73 (the watch receiver's data encrypted at rest), plus the fixes in this
  branch. The watch receiver's own endpoint (`watch/receiver.py`) is out of scope; its outside connections are #59.
- **Date:** November 2026.

**Results**

| Result | Meaning |
|---|---|
| **Met** | The requirement holds. The reason says where in the code. |
| **Fixed** | Not met on `main`. Fixed in the branch that added this document (see [Fixes made](#fixes-made)). |
| **Issue #n** | Not met. The work, or the decision, is in that issue. |
| **Accepted** | Not met as written, on purpose for a self-hosted household app; the owner confirmed each one in #78 (see [Accepted deviations](#accepted-deviations)). |
| **N/A** | Sanguity has no such feature. |

## Summary

Phase 1 holds up well. Access to records is checked per profile on every route, and a profile that isn't shared
with you is reported as not found. Tokens are random and stored only as hashes. Cookies are HttpOnly and
SameSite=Strict, and every state-changing call has to pass a CSRF check. Passwords use Argon2id with a breached
password list. Documents are served under a CSP that runs no scripts. Health data at rest is AES-256-GCM, with
the key kept apart from the data.

The review found six gaps that were small enough to fix here:

1. No security events went to the log.
2. The pages' CSP allowed inline scripts.
3. One-time passwords never expired.
4. The session cookie had no `__Host-` prefix.
5. API answers had no `Content-Disposition`.
6. Importing a zip had no size limit.

The larger gaps, and those that need your decision, are in issues #74 to #78.

## Fixes made

Each fix has a test in `tests/test_api.py` or `tests/browser_check.py`.

| Fix | ASVS | Change |
|---|---|---|
| Security event log | 7.1.3, 7.1.4, 7.2.1, 7.2.2, 2.8.5 | Each security event becomes one JSON line on the app's output, so `docker compose logs app` shows it. The events: sign-ins and how they were made, failed passwords, codes, recovery codes and passkeys, a reused authenticator code, throttled tries, a wrong password when re-checked, refused access to a profile or an admin route, and refused cross-site requests. The lines never hold a password, code or token. They name a username only when it matches an account, because a name that matches none may be a password typed into the wrong field. JSON encoding means a username can't add lines to the log (`main._audit`). |
| No inline script | 14.4.3, 5.3.3 | The pages' CSP is now `script-src 'self'`, without `'unsafe-inline'`. The inline scripts moved to `www/js/theme.js`, `login.js` and `setup.js`. The dev server sends the same CSP (`main.PAGE_CSP`), and a test checks that it matches `nginx.conf`. The browser check runs the setup, sign-in, records and account pages under the real CSP and expects no violations. |
| One-time passwords expire | 2.3.1 | A one-time password from an admin (the Account page or `server.manage`) stops working after `TEMP_PASSWORD_DAYS` (7 days by default). After that, sign-in says to ask an admin for a new one. Schema version 7 adds `users.temp_pw_expires`. One-time passwords issued before this version keep working until they're replaced. |
| `__Host-` session cookie | 3.4.4 | Over HTTPS the session cookie is `__Host-sg_session`, so the browser holds it to Secure, `Path=/` and this exact host. Plain HTTP can't use the prefix, so there it stays `sg_session`. A cookie from before the change is still read, so nobody is signed out by the upgrade, and the next HTTPS sign-in replaces it. |
| API answers download | 14.4.2 | Every `/api/` answer that doesn't set its own carries `Content-Disposition: attachment; filename="api.json"`. Documents still say `inline`. |
| Zip size limit | 12.1.2 | `server.manage import` refuses an export zip that unpacks to more than 100,000 files or 16 GB, before writing anything. |

## Issues filed

All five are in the Phase 1 milestone.

- #74 Pepper password hashes and strengthen recovery codes (2.4.5, 2.6.2): fixed; the pepper is kept encrypted with the data key, and recovery codes stay 8 characters, now salted
- #75 Second factor for admin actions (4.3.1, 4.3.3)
- #76 Decide session lifetime defaults (3.3.2): decided, see [Accepted deviations](#accepted-deviations)
- #77 Automatic dependency updates, and an update path that pulls patched images (14.2.1): done, see 14.2.1
- #78 Confirm or change the accepted ASVS deviations (2.1.1, 6.4.2, 8.1.4, 9.1.1, 9.2.2, 14.4.5): decided, see [Accepted deviations](#accepted-deviations)

## Accepted deviations

Decided by the owner in #78, and for 3.3.2 in #76:

| # | Decision | Why |
|---|---|---|
| 2.1.1 | Accepted as is: 15 characters, or 8 with a second factor | NIST SP 800-63B's rule. A short password must be replaced if the second factor is removed. |
| 6.4.2 | Accepted; the README documents keeping the key in a secret store | A vault or HSM is unusual on a home server. `SANGUITY_KEY_FILE` can name a Docker/Podman secret or a systemd credential, since those are files too. |
| 3.3.2 | Changed (#76): 12 hours without a second factor; 30 days with one, and no idle limit, accepted | On a shared household tablet, signing in again every 12 hours or after 30 idle minutes would be constant friction. A second factor stops a stolen password, so long sessions are kept for accounts that have one; password-only accounts get the 12 hours ASVS asks for. Removing the second factor (or upgrading) ends sessions older than 12 hours. All three limits can be changed in `.env`. |
| 8.1.4 | Changed: admins see bursts of failed sign-ins | `alerts.py`, `/api/security/notice`, shown under **Account**. Per-endpoint rate limits for signed-in requests stay out of scope. |
| 9.1.1, 9.2.1–9.2.2, 14.4.5 | Accepted: HTTPS is the proxy's; plus a plain-HTTP warning and proxy guidance | The pages warn when opened over plain HTTP from outside the home network or a tailnet; the README covers `COOKIE_SECURE=1` and HSTS. nginx to the app stays on the compose network. |

## Deployment defaults

The issue asked for these items in particular.

- **nginx headers** (`nginx.conf`): the pages are sent with `Cache-Control: no-store`, `X-Robots-Tag: noindex`,
  `nosniff`, `Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`. They also get a CSP with
  `default-src 'self'`, `script-src 'self'` (fixed), `object-src 'none'`, `base-uri 'none'` and
  `frame-ancestors 'none'`. `style-src` still allows `'unsafe-inline'`, because the charts set inline styles; that
  can't run script. `server_tokens off` hides the nginx version. Only the four pages, `css/`, `js/` and `/api/`
  are served; everything else is a 404. HSTS isn't sent, because nginx speaks plain HTTP and HTTPS is your proxy's
  job (Accepted; the README says how to turn it on at the proxy).
- **Cookie flags behind a reverse proxy:**
  - The session cookie is HttpOnly, SameSite=Strict and `Path=/`, and gets the `__Host-` prefix over HTTPS (fixed).
  - Secure is set when the request came over HTTPS. The app learns that from `X-Forwarded-Proto`, which nginx
    passes on from your proxy, or uses its own scheme. `COOKIE_SECURE=1` forces it on.
  - A client can send `X-Forwarded-Proto` itself when there's no proxy in front. That can only change its own
    cookie, never anyone else's.
  - The device cookie (`sg_device`) only separates one browser's sign-in tries from other people's; it's sent
    only to `/api/login` and lets nobody in.
- **`PUBLIC_ORIGINS` and `X-Forwarded-Host`:**
  - These only widen which `Origin` the CSRF check accepts (`main._same_site`).
  - The check's real strength is the `X-Sanguity` header. A cross-site page can't send it without a CORS
    preflight, and the app never grants one.
  - A client that forges `X-Forwarded-Host` only affects its own requests.
  - Passkeys take the site name from the browser's own `Origin`, and the browser checks that against the
    passkey.
- **Trusting a proxy's client addresses:**
  - nginx replaces `X-Forwarded-For` with the address of the connection itself (`$remote_addr`). uvicorn trusts
    that header from the compose network only, where nginx is the only thing that can reach the app (no published
    port; `FORWARDED_ALLOW_IPS` in `compose.yaml`).
  - Behind your own proxy, every visitor therefore shares the proxy's address. The sign-in throttle allows for
    that: browsers that have signed in before are counted on their own.
  - `nginx.conf` shows how to trust a specific proxy with `set_real_ip_from`. Trusting a whole range that clients
    can reach would let them pick their own address, and so dodge the throttle.
- **Update path:** `git pull && docker compose pull --ignore-buildable && docker compose build --pull && docker
  compose up -d`, at least monthly (README). It pulls the newest nginx image and rebuilds the app on the newest
  Python base image, so a moving tag's security patches arrive. Python packages are pinned with hashes, and
  Dependabot proposes updates weekly for them, the images and the GitHub Actions (#77).
- **Containers:**
  - The app and nginx run as unprivileged users on read-only filesystems, with only `/tmp` writable.
  - The app has no published port.
  - The key file is mounted read-only, apart from the data.
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
| 2.2.1 | Anti-automation against guessing | Met | `security.Throttle`: per account and address (5 free tries), per address (20), per account (30), then waits that double up to 15 minutes. Browsers that signed in before are counted on their own, so strangers can't lock an owner out. Two-factor tickets die after 5 wrong codes. At most a few password hashes run at once (`HASH_PARALLEL`). |
| 2.2.2 | Weak authenticators (SMS, email) limited | N/A | There are none. |
| 2.2.3 | Notify after authentication changes | N/A | Sanguity has no email or other channel. Changes are in the account activity log for admins (`2fa.on`, `password`, `passkey.add`, ...), and the person sees their own sessions and passkeys. |
| 2.3.1 | One-time passwords random and short-lived | **Fixed** | 3 × 8 random URL-safe characters (`accounts.temp_password`), which must be replaced at first sign-in. They now expire after `TEMP_PASSWORD_DAYS`. |
| 2.3.2 | Users can enrol their own authenticators | Met | Authenticator app (`/api/me/2fa/*`) and up to 20 passkeys (`passkeys.PER_ACCOUNT`). |
| 2.3.3 | Renewal of time-bound authenticators | N/A | None are time-bound. |
| 2.4.1 | Passwords stored with an approved slow hash | Met | Argon2id with argon2-cffi's parameters (RFC 9106 low-memory profile), rehashed when they change (`security.needs_rehash`). |
| 2.4.2 | Salt of at least 32 bits | Met | 128-bit random salt per hash (argon2-cffi). |
| 2.4.3 / 2.4.4 | PBKDF2 / bcrypt parameters | N/A | Argon2id is used. |
| 2.4.5 | Extra step with a secret salt (pepper) | Fixed (#74) | Passwords are stored as Argon2id of HMAC-SHA256(pepper, password) (`security.hash_password`). The pepper is 256 random bits, made on first start and kept in the `meta` table encrypted with the data key (`atrest.load_pepper`), so a copied database can't be checked against guesses without the key file. It never changes: a key rotation re-encrypts it (`atrest.seal_everything`). Hashes from before it are replaced at the next sign-in (`security.needs_rehash`). |
| 2.5.1 | Activation and recovery secrets not sent in clear | Met | One-time passwords are shown once to the admin, never stored in clear and never sent anywhere. |
| 2.5.2 | No password hints or security questions | Met | None. |
| 2.5.3 | Recovery doesn't reveal the password | Met | An admin reset makes a new one-time password. |
| 2.5.4 | No shared or default accounts | Met | The first account is created in the browser with a one-time setup code from the log (about 60 bits, throttled). That code is removed once the account exists (`main._setup_code`). |
| 2.5.5 | Notify when a factor changes | N/A | No channel, as for 2.2.3. |
| 2.5.6 | Forgotten password recovery is secure | Met | Only an admin, or the command line, can reset. Every session and known browser of the account ends (`accounts.set_temp_password`). |
| 2.5.7 | Lost authenticator needs identity checks | Met | Recovery codes, or an admin's reset (`disable_2fa`), which removes the authenticator app and every passkey and signs the person out everywhere. |
| 2.6.1 | Lookup secrets single-use | Met | `recovery_codes.used_at`. Using one also signs out every other session. |
| 2.6.2 | Lookup secrets of 112 bits, or salted | Fixed (#74) | Recovery codes stay 8 characters (about 40 bits), so they're easy to type. Each is stored with its own random 128-bit salt as HMAC-SHA256(pepper, salt + code) (`twofactor.recovery_hash`), so a copied database alone gives nothing to search. Codes made before this are plain SHA-256 and keep working until the account's codes are replaced. They only replace the second step, so the password is still needed. |
| 2.6.3 | Lookup secrets not guessable | Met | `secrets.choice`, throttled per account. |
| 2.7.x | Out-of-band verifiers | N/A | None. |
| 2.8.1 | Time-based OTPs have a short life | Met | 30-second steps, plus or minus one step for clock drift (`twofactor.DRIFT`). |
| 2.8.2 | OTP secrets protected | Met | Encrypted at rest with AES-GCM (`users.totp_secret`, in `atrest.FIELDS`). A pending secret only takes effect once a code from it is confirmed. |
| 2.8.3 | Approved algorithms | Met | HMAC-SHA-1 TOTP (RFC 6238), the standard for authenticator apps. |
| 2.8.4 | OTP used only once | Met | Steps at or before `totp_last_step` are refused. |
| 2.8.5 | Reuse of an OTP is logged | **Fixed** | Refused before. Now also logged as `login.fail` with reason `code already used`. |
| 2.8.6 | OTP devices can be revoked | Met | The person can turn it off (with their password), or an admin can (`disable_2fa`). |
| 2.9.1 | Crypto authenticator keys protected | Met | Passkeys keep the private key on the device. Only the public key is stored (`passkeys.register`). |
| 2.9.2 | Challenge nonce at least 64 bits | Met | py_webauthn's 64-byte random challenge. Single use, 5 minutes, kept on the server under a random state token (`passkeys._Challenges`). |
| 2.9.3 | Approved algorithms | Met | py_webauthn's defaults (ES256, RS256, EdDSA). User verification is required to create a passkey and to sign in without a password. The site name and origin are checked against the request's Origin, which the CSRF check has matched to this site. |
| 2.10.1 | No shared secrets between services | Met | The app and nginx share no credential. |
| 2.10.2 | No default credentials | Met | None. |
| 2.10.3 | Service secrets stored securely | Met | The data key is a mode-600 file outside the data, mounted read-only (`compose.yaml`). |
| 2.10.4 | No secrets in source or config | Met | Keys and codes are generated at install time. `.env.example` holds none. |

## V3 Session management

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 3.1.1 | Session token never in a URL | Met | Cookie only. Two-factor tickets and passkey states go in request bodies. |
| 3.2.1 | New token at sign-in | Met | `main._start_session` at every sign-in. Turning two-factor sign-in off ends every session and starts a fresh one. |
| 3.2.2 | At least 64 bits of entropy | Met | 256 bits (`secrets.token_urlsafe(32)`). |
| 3.2.3 | Stored safely in the browser | Met | HttpOnly cookie. Only its SHA-256 is in the database. |
| 3.2.4 | Approved random generator | Met | `secrets`. |
| 3.3.1 | Sign-out ends the session on the server | Met | `POST /api/logout` deletes the session row. |
| 3.3.2 | Re-authentication after 12 hours, or 30 minutes idle | **Changed** (#76; partly Accepted) | 12 hours from sign-in on an account without a second factor (`SESSION_HOURS_WITHOUT_2FA`), 30 days on one with an authenticator app or passkey (`SESSION_DAYS`), judged from sign-in on every request (`main._lifetime`). The idle limit (`SESSION_IDLE_MINUTES`) is off by default. See [Accepted deviations](#accepted-deviations). |
| 3.3.3 | Option to end other sessions after a password change | Met | They end automatically (`change_password`). |
| 3.3.4 | Users can see and end their sessions | Met | `GET/DELETE /api/sessions`, `revoke-others`, and the Account page. |
| 3.4.1 | Secure attribute | Met | Set over HTTPS (`main._secure`). `COOKIE_SECURE=1` forces it. |
| 3.4.2 | HttpOnly attribute | Met | Session and device cookies (test `test_cookie_flags`). |
| 3.4.3 | SameSite attribute | Met | `Strict`. |
| 3.4.4 | `__Host-` prefix | **Fixed** | Over HTTPS, as described above. |
| 3.4.5 | Path set narrowly | Met | The app owns the whole host. The device cookie is limited to `/api/login`. |
| 3.5.1–3.5.3 | OAuth, static API keys, JWT | N/A | None. |
| 3.7.1 | Re-check before sensitive changes | Met | Changing the password needs the current one. Turning off 2FA and adding a passkey need the password. New recovery codes need a code. Deleting a profile needs its name typed. The admin side: #75. |

## V4 Access control

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 4.1.1 | Enforced on a trusted service | Met | Every check is in the API. nginx serves no data (`nginx.conf`, last `location`). |
| 4.1.2 | Access-control data can't be changed by the client | Met | Roles come from `memberships` on the server. The admin flag is changed by admins only, never by yourself (`edit_user`). |
| 4.1.3 | Least privilege | Met | Viewer, editor and owner per profile (`main.need`). Admin manages accounts but sees no records. Ownerless profiles show names and members only (`/api/ownerless`). |
| 4.1.5 | Fails securely | Met | Errors are raised before any data is read. A profile that isn't shared with you is a 404 (`need`). |
| 4.2.1 | No insecure direct object references | Met | Every profile route calls `need()`. Documents are looked up by random id within the profile (`_document`). Sessions and passkeys are matched to the caller's `user_id`. Tests cover access across accounts. |
| 4.2.2 | CSRF protection | Met | `X-Sanguity` header plus Origin check on every state-changing `/api/` call (`main.guard`), and SameSite=Strict (`test_csrf`). |
| 4.3.1 | Admin interfaces use multi-factor | **Issue #75** | Only when the server requires 2FA for everyone. |
| 4.3.2 | No directory browsing or metadata files | Met | nginx serves an allow-list of paths, with no autoindex. `.git` and the config files aren't reachable. |
| 4.3.3 | Step-up for lower-value systems | **Issue #75** | Same as 4.3.1. |

## V5 Validation, sanitization and encoding

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 5.1.1 | HTTP parameter pollution | Met | FastAPI and pydantic take one typed value per parameter. |
| 5.1.2 | Mass assignment | Met | Each route has its own pydantic model with only the fields it accepts (`UserPatch`, `MemberIn`, ...). |
| 5.1.3 | Input validated by allow-list | Met | Roles are a `Literal`. Usernames have a character and length check (`security.username_problem`). Profile and passkey names have length limits. Ids are typed as int. nginx caps request bodies at 64 KB. |
| 5.1.4 | Structured data typed | Met | pydantic. |
| 5.1.5 | Redirects only to allowed places | Met | The pages redirect only to fixed local pages. No redirect takes a URL parameter. |
| 5.2.1–5.2.3 | Rich text, unstructured input, SMTP | N/A | None. |
| 5.2.4 | No eval of user data | Met | None in the server or the pages. The pages' CSP now forbids inline script, and eval isn't allowed either. |
| 5.2.5 | Template injection | N/A | No server-side templates. |
| 5.2.6 | SSRF | N/A | The app makes no outbound requests. |
| 5.2.7 | SVG scriptable content | Met | Uploaded SVG isn't served (not in `DOC_TYPES` or `PART_TYPES`). The QR code is an SVG the server draws itself, shown as an image. |
| 5.2.8 | Template languages | N/A | None. |
| 5.3.1 | Output encoded for its context | Met | The pages build the DOM with `textContent` and attributes (`www/js/util.js` `el()`). There's no `innerHTML` in `www/js`. |
| 5.3.3 | Protection against reflected, stored and DOM XSS | Met | As 5.3.1. Records are shown as text. Documents run under `script-src 'none'` (`main.DOC_CSP`). |
| 5.3.4 | Parameterized database queries | Met | Every value is a `?` parameter. The only f-strings in SQL hold fixed table and column names (`atrest.FIELDS`, `activity._rows`). |
| 5.3.6–5.3.7, 5.3.10 | JSON, LDAP and XPath injection | N/A | None. |
| 5.3.8 | OS command injection | Met | The app runs no commands. Setup (`docker/setup.py`) runs fixed argument lists, with no shell. |
| 5.3.9 | Local file inclusion | Met | Document paths are fixed at import and checked to be inside the exports or build folder (`profiles.resolve_document`, `_inside` with `realpath`). Pictures an HTML record links to must be in its own folder and of an allowed type (`document_part`). Tested in `test_document_paths`. |
| 5.5.1–5.5.3 | Deserialization | Met | JSON only. No pickle or YAML. `build.py` parses your own exports with the standard library's expat, which doesn't load external entities. |

## V6 Stored cryptography

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 6.1.1–6.1.2 | Private and health data encrypted at rest | Met | AES-256-GCM for every file Sanguity writes and every sensitive database field (`crypto.py`, `atrest.py`). What isn't covered is listed in `crypto.py`: usernames and profile names, your own original exports (read-only) and downloads from "Export everything". The watch receiver's data uses the same key since #73. |
| 6.1.3 | Financial data encrypted | Met | The costs are part of a profile's encrypted records. |
| 6.2.1 | Fails securely | Met | A failed GCM tag check raises an error, and nothing is returned. Start-up stops with a reason if the key is missing or wrong (`atrest.check_key`). |
| 6.2.2 | Proven primitives | Met | pycryptodome AES-GCM. Python's `secrets`, `hashlib` and `hmac`. |
| 6.2.3 | Safe modes, IVs and padding | Met | GCM with a random 96-bit nonce for each encryption, and a 128-bit tag. |
| 6.2.4 | Algorithms can be replaced | Met | Formats carry a version marker (`SGE1`, `enc:1:`) and a key id. Keys rotate with `setup new-key` and re-encryption at start-up. |
| 6.2.5 | No weak algorithms | Met | SHA-1 only inside HMAC for TOTP, as RFC 6238 specifies. |
| 6.2.6 | Nonces unique | Met | 96 random bits each time. The volume per key is far below the 2³² limit for random nonces. |
| 6.2.7 | Encrypted data authenticated | Met | GCM. |
| 6.2.8 | Constant-time comparisons | Met | `hmac.compare_digest` for the setup code and TOTP. Tokens are looked up by their hash. |
| 6.3.1 | Approved random generator | Met | `secrets` everywhere. |
| 6.3.2 | Random GUIDs | N/A | None. |
| 6.4.1 | Key management | Met | Key file outside the data and the backups, mode 600, rotation documented (`crypto.py`, README). |
| 6.4.2 | Key kept in an isolated module | Accepted | The key is a file (`SANGUITY_KEY_FILE`, default `auth/data_key`, mode 600, read-only mount) held in the app's memory. The README shows how to keep it in a Docker/Podman secret or a systemd credential instead. |

## V7 Errors and logging

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 7.1.1 | No credentials or session tokens in logs | Met | uvicorn's access log is off. The new security log leaves out passwords, codes, tokens and usernames that match no account (`test_security_log`). |
| 7.1.2 | No other sensitive data in logs | Met | Start-up and build messages name profiles by number. Health data isn't logged. |
| 7.1.3 | Security events logged | **Fixed** | Before: only the activity log (changes, views, account events), with nothing on sign-ins or refusals. Now `main._audit` (see [Fixes made](#fixes-made)). |
| 7.1.4 | Enough detail to investigate | **Fixed** | Each line has the time, event, address, account and reason. |
| 7.2.1 | Authentication decisions logged | **Fixed** | `login.ok` (with method), `login.fail` (with step and reason), `login.throttled`, `setup.fail` and `reauth.fail`. |
| 7.2.2 | Access-control failures logged | **Fixed** | `access.denied` (profile, role needed) and `csrf.refused`. |
| 7.3.1 | Log injection prevented | Met | Each event is one JSON-encoded line. Activity details are stored as JSON and shown with `textContent`. |
| 7.3.3 | Logs protected | Met | Docker's own log storage on the host. The activity log is readable only by profile owners (their profile) and admins (account events). |
| 7.3.4 | Time synced | N/A | The host's clock. |
| 7.4.1 | Generic error message | Met | Unhandled errors return FastAPI's plain "Internal Server Error". Debug mode and tracebacks are never sent. |
| 7.4.2 | Exception handling | Met | Known failures map to clear 4xx answers (`AccountError`, `PasskeyError`, `Busy`). A bad build keeps the last good records and logs the problem (`profiles.sync_build`). |
| 7.4.3 | Last-resort handler | Met | Starlette's. |

## V8 Data protection

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 8.1.1 | Sensitive data kept out of caches | Met | `Cache-Control: no-store` on pages and the API. |
| 8.1.2 | Cached copies on the server purged | N/A | No server cache. |
| 8.1.3 | Few parameters, no sensitive ones in URLs | Met | Sensitive values go in request bodies. |
| 8.1.4 | Abnormal request volume detected | Fixed (#78) | Failed sign-ins are throttled, logged and kept 30 days (`alerts.py`); admins see a notice under **Account** when one address or account fails 10 times in a day, or 30 failures pile up (`/api/security/notice`). Other endpoints need a signed-in session and aren't rate-limited (Accepted). |
| 8.1.5 | Backups made | Met | `server.manage backup`: a consistent SQLite copy plus files, with a manifest of SHA-256 hashes. Verify and restore are tested. |
| 8.1.6 | Backups stored securely | Met | The data in them is encrypted, and the key isn't included. |
| 8.2.1 | Anti-caching headers | Met | As 8.1.1. |
| 8.2.2 | Nothing sensitive in browser storage | Met | `localStorage` holds view preferences only: theme, date range, tab, which lab series are picked. No records or tokens. |
| 8.2.3 | Browser data cleared at sign-out | Met | No sensitive data is stored in the browser (see 8.2.2). Session cookies are deleted. |
| 8.3.1 | Sensitive data in bodies or headers, not URLs | Met | As 8.1.3. |
| 8.3.2 | Users can export and delete their data | Met | Export everything (owners and editors), delete a profile (owners), and remove an account (admins). Leaving a profile removes your own access. |
| 8.3.3 | Clear language on what's collected | N/A | Self-hosted: the install's owner holds the data. The README says what's stored and where. |
| 8.3.4 | Sensitive data identified | Met | Listed in `crypto.py`, `activity.py` and `backup.py`. |
| 8.3.5 | Access to sensitive data audited | Met | Views and document opens are logged once per session per profile, readable by owners (`activity.once`). |
| 8.3.6 | Sensitive data cleared from memory | N/A | Python offers no reliable way. |
| 8.3.7 | Sensitive data encrypted | Met | As V6. |
| 8.3.8 | Retention | Met | The activity log keeps 365 days (`ACTIVITY_DAYS`). Records are kept until their owner deletes them. |

## V9 Communications

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 9.1.1 | TLS for all client connectivity | Accepted | HTTPS is the owner's reverse proxy or Tailscale, and passkeys need it. The sign-in and Account pages warn when opened over plain HTTP from outside the home network or a tailnet (`plainHttpWarning` in `www/js/util.js`); the README covers `COOKIE_SECURE=1` and HSTS at the proxy. |
| 9.1.2–9.1.3 | Strong TLS settings | N/A | Sanguity terminates no TLS. |
| 9.2.1–9.2.2 | Server-to-server TLS | Accepted | nginx to the app runs over the compose network. There are no outbound connections. |

## V12 Files and resources

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 12.1.1 | Large files refused | Met | The browser can't upload: request bodies are capped at 64 KB. Imports are from the command line. |
| 12.1.2 | Compressed files checked | **Fixed** | `export.import_archive` now checks the total unpacked size and the number of files. Names were already checked against an allow-list, with no `..`. |
| 12.1.3 | Per-user quota | N/A | No uploads. |
| 12.2.1 | File type checked | N/A | No uploads. Served types come from a fixed table, and anything else downloads as `application/octet-stream`. |
| 12.3.1 | Submitted file names not used directly | Met | Documents are reached by random id. Part names are resolved inside the record's folder (`document_part`). |
| 12.3.2 | No path traversal | Met | `_inside` with `realpath` (tests: `test_document_paths`, `test_html_record_loads_its_own_pictures_and_styles`). |
| 12.3.3 | No remote file inclusion | N/A | The app fetches nothing. |
| 12.3.4 | No reflected file download | Met | `Content-Disposition` carries the quoted original name. API answers are now `attachment` (14.4.2). |
| 12.3.5–12.3.6 | OS commands and file paths | N/A | None. |
| 12.4.1 | Files stored outside the web root | Met | nginx serves only `www/`. Records are reached through the API. |
| 12.4.2 | Antivirus scan | N/A | No uploads. Documents are your own exports. |
| 12.5.1 | Only allowed file types served | Met | nginx allow-list. The API's `DOC_TYPES` and `PART_TYPES`. |
| 12.5.2 | Uploaded files never run | Met | Documents are served under `script-src 'none'`, with `nosniff`. |
| 12.6.1 | SSRF allow-list | N/A | No outbound requests. |

## V13 API and web service

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 13.1.1 | Same encoding throughout | Met | JSON over UTF-8. |
| 13.1.3 | No sensitive data in API URLs | Met | Ids only. The passkey list's `origin` parameter is display-only. |
| 13.1.4 | Authorization at both route and resource level | Met | Dependencies (`user`, `admin`), then `need()` per profile. |
| 13.1.5 | Unexpected content types refused | Met | FastAPI accepts only JSON bodies for its models. |
| 13.2.1 | Only intended HTTP methods | Met | Others get 405. |
| 13.2.2 | JSON schema validation | Met | pydantic models. |
| 13.2.3 | CSRF protection | Met | As 4.2.2. |
| 13.2.5 | Content type checked | Met | As 13.1.5. |
| 13.2.6 | Message integrity | N/A | No signed messages. |
| 13.3.x, 13.4.x | SOAP, GraphQL | N/A | None. |

## V14 Configuration

| # | Requirement | Result | Why / where |
|---|---|---|---|
| 14.1.1 | Repeatable builds | Met | Dockerfile, hash-pinned requirements, CI builds and runs the stack (`.github/workflows/check.yml`). |
| 14.1.3 | Hardened server configuration | Met | Unprivileged users, read-only filesystems, no published app port, `server_tokens off`. |
| 14.1.4 | Redeploy is automated | Met | `docker compose up -d --build`. |
| 14.2.1 | Components up to date | Met | `.github/dependabot.yml`: weekly proposals for the Python locks (uv ecosystem, which keeps the hashes and `--universal`; CI checks the locks match `requirements.in`), the Dockerfile and `compose.yaml` images and the GitHub Actions. The README's update command pulls newer images and base images (#77). |
| 14.2.2 | Unneeded features off | Met | No API docs or OpenAPI. Static serving only in development (`SANGUITY_SERVE_STATIC`). |
| 14.2.3 | Subresource integrity for outside assets | N/A | Nothing is loaded from elsewhere (`default-src 'self'`). |
| 14.2.4 | Components from trusted sources | Met | PyPI with `--require-hashes`. Official Python and nginx images. |
| 14.2.5 | Inventory of components | Met | `requirements.txt` (with hashes) and the image tags. |
| 14.2.6 | Third-party components isolated | Met | Separate containers. The app has no published port and only nginx can reach it. |
| 14.3.1 | Generic server errors | Met | As 7.4.1. |
| 14.3.2 | Debug modes off | Met | Nothing enables them. |
| 14.3.3 | No version headers | Met | `server_tokens off`. uvicorn `server_header=False`. |
| 14.4.1 | Content-Type with charset | Met | JSON, and documents' text types carry `charset=utf-8`. |
| 14.4.2 | API answers carry `Content-Disposition: attachment` | **Fixed** | `main.guard`. |
| 14.4.3 | CSP | **Fixed** | No inline script. See [Deployment defaults](#deployment-defaults). |
| 14.4.4 | `nosniff` | Met | nginx and the API. |
| 14.4.5 | HSTS | Accepted | Your HTTPS proxy's job; the README gives the header to set there. |
| 14.4.6 | Referrer-Policy | Met | `no-referrer`. |
| 14.4.7 | Framing controlled | Met | Pages: `frame-ancestors 'none'` and `DENY`. Documents: framed by the app only (`SAMEORIGIN`, `frame-ancestors 'self'`). |
| 14.5.1 | Only needed methods | Met | As 13.2.1. |
| 14.5.2 | Origin not the only access control | Met | Origin is checked in addition to the custom header and the session. |
| 14.5.3 | CORS allow-list | Met | No CORS headers at all. |
| 14.5.4 | Proxy headers only from trusted proxies | Met | See [Deployment defaults](#deployment-defaults). `X-Forwarded-For` is set by nginx itself, and uvicorn trusts it from the compose network only. |
