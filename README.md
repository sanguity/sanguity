# Sanguity

**Your medical records, insurance claims and wearable data on one private timeline, on your own server.**

Sanguity takes the exports you can download from patient portals and insurers, plus watch and smart-scale
data from your phone, and turns them into one place to see lab trends, visits, hospital stays, documents and
what your care actually cost. It runs on hardware you control; nothing is sent anywhere else.

![Overview: key lab tiles with sparklines, watch and scale summaries, problems and upcoming visits](docs/screenshots/overview.png)

> **Not medical advice.** Sanguity shows what your records say. It does not diagnose, interpret results or
> recommend treatment. Highlighted values are flagged against the reference ranges in your own lab reports;
> your care team interprets them in context.

All screenshots use the built-in **fictional demo data**. No real person's records appear in this repository.

## What you get

| Tab | What it shows |
|---|---|
| Overview | Key labs with sparklines, watch and scale summaries, active problems, upcoming visits, costs since a milestone |
| Health | Watch and smart-scale data: weight and body composition, sleep stages, steps and workouts, heart rate and HRV |
| Timeline | Every visit, stay, lab draw, report, message, document and claim on one zoomable timeline, with a searchable list |
| Trends | Lab results over time against reference ranges, with procedures, imaging and your milestones marked |
| Lab heatmap | Every draw against its reference range, one row per lab |
| Costs | Insurance claims by month, type of care and provider, and what you actually paid out of pocket |

![Trends: blood counts dipping through treatment cycles, with milestone markers](docs/screenshots/trends.png)

Charts on Trends, Health and the heatmap move together: drag to pan, scroll or pinch to zoom, double-click to
see everything. **Milestones** (diagnosis, surgery, treatment start…) appear on every chart and as "since"
periods.

**Several people, one install.** Each person who signs in has their own account, and each person whose health
is tracked has a **profile**. One account can look after several profiles (a parent and a child), and a profile
can be shared with several accounts (a caregiver), each as **owner** (view, change and share), **editor** (view
and change) or **viewer** (view only). Everyone sees only the profiles shared with them; switch between them
from the header.

| Health | Timeline | Costs |
|---|---|---|
| ![Health tab](docs/screenshots/health.png) | ![Timeline tab](docs/screenshots/timeline.png) | ![Costs tab](docs/screenshots/costs.png) |

## Install (Docker)

```sh
git clone https://github.com/sanguity/sanguity.git && cd sanguity
cp .env.example .env            # optional: ports (default 8730 web, 8731 phone uploads), folders, time zone, your user id
docker compose up -d --build
docker compose logs app         # shows the one-time setup code
```

Open `http://<this machine>:8730` and enter the setup code to create the first account. It becomes the admin
and owns the records already on the server; until you add your own, that is the fictional demo. Add accounts for
other people under **Account**, then share profiles with them there.

**Your records:** put each export in its own folder under `RECORDS_DIR` (default `./records`), copy
`sources.example.json` to `sources.json` and describe them, then `docker compose run --rm setup rebuild`; the first profile picks up the rebuild automatically.
Optional: `corrections.json` (milestones and hand corrections; see `corrections.example.json`) and
`settings.json` (that profile's dashboard preferences; see `settings.example.json`), both next to `compose.yaml`.

| Command | What it does |
|---|---|
| `docker compose run --rm setup rebuild` | Rebuild the data after adding or updating exports |
| `docker compose exec app python -m server.manage reset-password NAME` | One-time password for a locked-out account |
| `docker compose exec app python -m server.manage list` | Accounts, profiles and who has which role |
| `docker compose exec app python -m server.manage disable-2fa NAME` | Turn off two-factor sign-in for someone who lost their phone and recovery codes |
| `git pull && docker compose up -d --build` | Update Sanguity |

**Upgrading from a version without accounts:**

- The old single login (`auth/htpasswd`) no longer works. After updating, `docker compose logs app` shows a setup
  code; the account you create with it owns your existing records.
- The default ports changed from 8080 and 8081 (often taken by other apps) to **8730** for the web app and **8731**
  for the phone app's uploads. If you relied on the old defaults, either keep them by adding `WEB_PORT=8080` and
  `RECEIVER_PORT=8081` to `.env`, or update your bookmarks, reverse proxy and the upload address in the phone app.
  Otherwise watch data quietly stops arriving.

### Supported sources today

| Source | What is read |
|---|---|
| Epic MyChart exports (one primary, any number of others) | Lab and test results, imaging and procedure reports, messages, letters, documents, C-CDA visit records |
| CareSpace / OncoEMR patient portal | Lab results, visits, medications, problems, vitals, documents |
| Insurance claims exports (`uhc`, `bcbs` formats) | Claims, billed / paid / your share, denials |
| Payments export (`payments.json`) | Out-of-pocket payments and premiums |
| Health Connect, via the Life Dashboard Companion phone app | Steps, heart rate, HRV, sleep stages, workouts, weight and body composition |

Standard formats (FHIR, C-CDA labs, Apple Health, CSV) and an import tool in the app are planned; if your
portal's export doesn't import, please open an issue with an anonymised sample.

## Privacy and security

- **Self-hosted only.** Your data stays on your server; Sanguity makes no outside requests and has no telemetry.
- **Accounts with Argon2id-hashed passwords** (at least 15 characters, per NIST SP 800-63B; passwords found in
  public breaches are refused, checked against a bundled list on your server, see `server/passwords/`), sign-in throttling,
  and server-side sessions you can see and sign out under **Account**. Optionally sign people out after a period
  of inactivity (`SESSION_IDLE_MINUTES` in `.env`).
- **Two-factor sign-in** with an authenticator app (codes that can't be reused) and one-time recovery codes, under
  **Account**; admins can require it for everyone. With it on, a password may be as short as 8 characters.
- **Every request for records or documents checks the account's role on that profile.** Documents are served
  only through that check, read-only, with scripts blocked; no folder of health data is served directly.
- **Admins manage accounts, not records:** being an admin doesn't show anyone's records.
- **Export everything:** owners and editors can download a profile as one zip (records, documents, settings, watch
  data and activity) under **Account**, and load it into another install with `server.manage create-profile` and
  `import`.
- **Activity log:** each profile's owners can see who opened its records and documents, and who shared, renamed or
  changed it, and when (**Account > Activity**); admins see account changes. Entries are kept for a year
  (`ACTIVITY_DAYS` in `.env`).
- **Secrets are generated on first start** (watch-receiver key, a one-time setup code).
- Records, built data, watch data, logins and secrets are never committed (see `.gitignore`).
- Put Sanguity behind HTTPS (a reverse proxy, Tailscale or similar) before using it outside your home network.
  If sign-in then says "Request refused (cross-site check)", set `PUBLIC_ORIGINS` in `.env` to the address you
  open it at; to see visitors' real addresses on the device list, trust your proxy in `nginx.conf` (see the comment there).

## Backup and restore

Health records are worth more than uptime: keep copies off this machine.

**What to back up**

| What | Where | How |
|---|---|---|
| Accounts, profiles, records as loaded | `DATA_DIR` (default `./data`) | `docker compose exec app python -m server.manage backup` writes one checked archive to `BACKUP_DIR` (default `./backups`), safe while the app runs; `--keep 14` keeps the newest 14 |
| Your original exports | `RECORDS_DIR` | copy them; Sanguity only reads them |
| Watch and scale data | `WATCH_DIR` | `watch.db` plus `raw/`, the receiver's archive of every upload (`receiver.py --reprocess` rebuilds the database from it) |
| Secrets and configuration | `auth/`, `.env`, `sources.json`, `corrections.json`, `settings.json` | copy them |

Run the backup every night, for example from cron on the host (on TrueNAS: **System > Advanced > Cron Jobs**):

```sh
0 3 * * * cd /path/to/sanguity && docker compose exec -T app python -m server.manage backup --keep 14
```

ZFS snapshots of these folders are fine too: SQLite recovers from a snapshot as from a power cut. The archive is
still worth having, since it can be checked (`python -m server.manage verify FILE`) and copied anywhere.

**Restore** with the app stopped; the data it replaces is moved to `DATA_DIR/before-restore-<time>/`, not deleted,
and a backup from an older version is upgraded when the app starts:

```sh
docker compose stop app web
docker compose run --rm --no-deps app python -m server.manage restore /backups/sanguity-backup-YYYYMMDD-HHMMSS.tar.gz
docker compose up -d
```

## Testing a branch beside your real install

`docker/dev-stack.sh` runs a separate test copy from any branch on the same machine: its own compose project
(`sanguity-dev`), ports (8740 and 8741 by default) and folders, so the real install is never touched. Your record
exports and watch summary can be shown read-only, or it uses the demo data.

```sh
curl -fsSLO https://raw.githubusercontent.com/sanguity/sanguity/main/docker/dev-stack.sh
sudo bash dev-stack.sh --dir /mnt/POOL/apps/sanguity-dev                       # main, demo data
sudo bash dev-stack.sh --dir /mnt/POOL/apps/sanguity-dev --branch my-feature   # switch to a branch (also: update)
sudo bash dev-stack.sh --dir /mnt/POOL/apps/sanguity-dev --records /path/to/exports     --watch-public /path/to/watch-data/public --puid UID --pgid GID              # your data, read-only
```

With `--records`, put your `sources.json` (and `corrections.json` / `settings.json` if you use them) in the
test copy's `config/` folder. `--down` stops it, `--logs` follows its logs, `--reset` starts it over; `--help`
lists everything.

On TrueNAS the script works without sudo: when Docker isn't reachable it deploys the test copy as a custom app
(it appears on the Apps page and starts with the server), and the setup code is in its `data/setup-code.txt`.
Keep `--dir` private (for example mode 750) when it holds a copy of your records.

## Development

```sh
pip install --require-hashes -r requirements-dev.txt && playwright install chromium   # exact, hash-checked versions
python3 -I demo/generate.py dev-data/build --watch dev-data/watch    # fictional demo data
SANGUITY_DATA=dev-data SANGUITY_RECORDS=dev-data/records SANGUITY_WATCH_FILE=dev-data/watch/daily.json   SANGUITY_SERVE_STATIC=1 PORT=8000 python3 -m server               # then open http://127.0.0.1:8000/ (setup code in the output)
python3 tests/test_api.py                      # API tests: sign-in, sessions, roles, sharing, documents
python3 tests/browser_check.py                 # the same browser check CI runs on every pull request
docker compose up -d --build && python3 tests/docker_smoke.py --code <setup code from docker compose logs app>
                                               # the whole install through nginx, as CI does too
```

```
compose.yaml        setup (one-shot), app (the API), web (nginx) and receiver services
server/             The API (FastAPI + SQLite): accounts, sessions, profiles, roles, records and documents
requirements.in     The API's dependencies; requirements.txt and requirements-dev.txt are their pinned,
                    hash-checked lock files (regenerate with uv, as requirements.in says)
docker/setup.py     First-run secrets and folders, data rebuilds
docker/dev-stack.sh A test copy from any branch beside a real install (see above)
build.py            Builds a profile's data.json from the exports listed in sources.json (standard-library Python 3)
lab-aliases.json    Canonical lab names, LOINC codes and other spellings that map onto them
nginx.conf          Web server: the app's pages and security headers; passes /api/ to the app
watch/receiver.py   Health Connect webhook receiver: archives payloads, folds them into SQLite, writes public/daily.json
www/                index.html (markup), css/app.css, js/*.js (one plain script per tab, loaded in order),
                    login.html, setup.html (first account), account.html (password, devices, profiles, accounts)
demo/               Fictional demo dataset generator
tests/              API tests, a headless browser check and a smoke test of the Docker install (all run in CI)
*.example.json      Templates for sources.json, corrections.json and settings.json; .env.example for .env
```

## License

Sanguity is free software under the [GNU Affero General Public License v3.0](LICENSE). If you run a modified
version for other people, for example as a hosted service, you must offer them its source code.
