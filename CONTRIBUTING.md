# Contributing to Sanguity

Thanks for helping. Sanguity holds people's medical records, so a few rules below are firm; the rest is ordinary
open-source practice. Questions are welcome as issues.

- **An export from your portal doesn't import?** Open a [portal-support issue](https://github.com/sanguity/sanguity/issues/new?template=portal-export.yml)
  with an anonymised sample: `tools/anonymise.py` makes one, and [docs/anonymising-exports.md](docs/anonymising-exports.md)
  says what it catches, what it doesn't, and what to check before you share it. **Never attach real records to an
  issue or pull request.**
- **A security problem?** Don't open an issue: see [SECURITY.md](SECURITY.md).
- **A bug or an idea?** Use the [bug report](https://github.com/sanguity/sanguity/issues/new?template=bug-report.yml)
  or [feature request](https://github.com/sanguity/sanguity/issues/new?template=feature-request.yml) form.

## Set up with the demo data

You need Python 3.12 or later. Everything runs on the **fictional demo data**; you never need real records to
develop.

```sh
git clone https://github.com/sanguity/sanguity.git && cd sanguity
pip install --require-hashes -r requirements-dev.txt && playwright install chromium   # exact, hash-checked versions
python3 -I demo/generate.py dev-data/build --watch dev-data/watch                       # fictional demo data
SANGUITY_DATA=dev-data SANGUITY_KEY_FILE=dev-keys/data_key SANGUITY_RECORDS=dev-data/records \
  SANGUITY_WATCH_FILE=dev-data/watch/daily.json SANGUITY_SERVE_STATIC=1 PORT=8000 python3 -m server
```

Open `http://127.0.0.1:8000/` and enter the setup code from the output to create the first account; it owns the
demo records. `dev-data/` and `dev-keys/` are ignored by git. To try a branch beside a real install instead, see
"Testing a branch beside your real install" in the [README](README.md#testing-a-branch-beside-your-real-install).

## Run the tests

CI ([`.github/workflows/check.yml`](.github/workflows/check.yml)) runs all of these on every pull request that can
affect them: a pull request that changes only documentation runs just the changelog check, one that changes only
the install templates (`deploy/`) just the templates job, and one that changes only tests or `tools/` skips the image
jobs ([`.github/scripts/changes.py`](.github/scripts/changes.py) has the rules). Pushes to `main`, releases and
changes to CI itself run everything. Run
them before you push:

```sh
python tests/test_api.py               # sign-in, sessions, roles, sharing, documents
python tests/test_uploads.py           # who may upload, refused paths and zips, limits, encryption, export
python tests/test_jobs.py              # background imports: format detection, failed imports leave nothing
python tests/test_preview.py           # import preview: duplicates, overlaps, cancel
python tests/test_manual.py            # lab results typed in
python tests/test_watch_store.py       # imported watch data
python tests/test_glucose.py           # blood glucose from the phone apps: meters, CGMs, time in ranges
python tests/test_import_history.py    # import history and undo
python tests/test_edits.py             # corrections made in the app
python tests/test_milestones.py        # milestones and workout rules on the Settings page
python tests/test_lab_mapping.py       # matching unknown lab names
python tests/test_summary.py           # the visit summary PDF
python tests/test_person.py            # About this person: birth date, sex for ranges, height history (#194)
python tests/test_backups.py           # backups in Admin: schedule, pruning, late warning, restore check
python tests/test_security_refresh.py  # the security review's checks: each route's access, columns encrypted or plain
python -m unittest discover -s tests -p "test_importer*.py" -v   # the registry and every importer's fixtures
python tests/test_anonymise.py         # tools/anonymise.py: every fixture's copy imports the same, nothing of it left
python tests/browser_check.py          # headless browser: every tab, the drawer, pan/zoom, every page at 375 px,
                                       # axe-core's WCAG 2.2 AA rules and a keyboard walk (docs/accessibility.md)
python tests/test_performance.py       # the performance budgets on ten years of fictional records (docs/performance.md)
```

CI also checks that the Python files compile, the JSON templates parse, `compose.yaml` is valid, the demo data is
the same on every run, and the lock files match `requirements.in`. A second job builds the Docker install and runs
`tests/docker_smoke.py` through nginx, which you can run too:

```sh
docker compose up -d --build && python3 tests/docker_smoke.py --code <setup code from docker compose logs app>
```

A new test file needs adding to the compile step and as its own step in `check.yml`, except importer tests
(`tests/test_importer_<name>.py`), which CI finds by themselves.

## Where things live

The README's [Development](README.md#development) section has the map of the repository. The two guides to read
first:

- **[docs/database.md](docs/database.md)**: the database design, with diagrams. Every table, which columns are
  encrypted, and what a deletion removes. A change to the schema (`SCHEMA` in `server/db.py`) is a new numbered
  step, and updates this page.
- **[docs/importers.md](docs/importers.md)**: how to add support for a new export.

### Writing an importer

An importer is **one Python file** in `server/importers/` plus **a fictional test fixture**; nothing else needs to
change. In short (the guide has the details):

- The module sets `NAME` and `LABEL` and defines `detect(files)`, a quick 0.0 to 1.0 score of how sure it is the
  upload is its format, and `parse(files)`, which returns the records (`events`, `details`, `labs`, `costs`...)
  and any warnings. Copy [`server/importers/sanguity.py`](server/importers/sanguity.py), the smallest one, to start.
- Give events and lab series a stable **source key** wherever you can, so the import preview spots duplicates, a
  later import updates records in place, and corrections made in the app find their event again.
- Treat every upload as hostile, use the standard library where you can, and keep the output deterministic.
- Put sample uploads under `tests/fixtures/importers/<NAME>/`. `tests/test_importers.py` checks the registry picks
  your importer for them and that the output keeps to the contract. Add `tests/test_importer_<name>.py` for edge
  cases.

## Rules this project keeps

- **Fictional data only, never real records.** Not in fixtures, screenshots, tests, issues, pull requests or commit
  messages. Fixtures and the demo use made-up people, providers, dates and values; anything derived from a real
  export has every name, date, ID and number changed. `.gitignore` keeps health-data file types out everywhere
  except `tests/fixtures/`, so whatever is there must be fictional.
- **Hash-checked dependencies, regenerated with uv.** Don't edit `requirements.txt` or `requirements-dev.txt` by
  hand. Change `requirements.in` (or `requirements-dev.in`) and regenerate the lock with the `uv pip compile`
  command written at the top of `requirements.in`. CI fails if a lock doesn't match. New dependencies need a good
  reason: importers in particular should use the standard library.
- **Strict Content Security Policy: no inline script.** Pages load scripts only from `www/js/` (`script-src
  'self'`): no `<script>` blocks, no `onclick="..."` attributes, no `eval`. Build markup with the `el()` helper
  and `textContent`, never by putting record text into `innerHTML`. The browser check fails on any CSP violation.
  The policy is in `nginx.conf` and `server/main.py` (`PAGE_CSP`); a test keeps the two the same.
- **Encrypt anything health-related.** Records, document names, settings, uploaded files and secrets are stored
  encrypted with the data key (`crypto.enc`, AES-256-GCM), in the database and on disk. A new column or file that
  holds anything about someone's health is encrypted too; only ids, timestamps, states and counts stay plain.
  [docs/database.md](docs/database.md#what-is-encrypted) lists what is. CI checks that no demo text is readable on
  disk, and `tests/test_security_refresh.py` that every new text column is either in `atrest.FIELDS` or listed as
  plain on purpose.
- **Every route says who may call it.** A new API route needs its session dependency (`user`, `admin`, ...) and, on a
  profile, a `need()` with the least role; `tests/test_security_refresh.py` lists every route and fails until the
  new one is added there and to [docs/security-review.md](docs/security-review.md#who-can-reach-what).
- **No outside requests.** Sanguity makes none and has no telemetry; a feature that needs one is opt-in.
- **Not medical advice.** Sanguity shows what the records say. It doesn't diagnose, interpret or recommend, and
  its wording shouldn't either.

## How changes are reviewed

1. For anything bigger than a small fix, open or find an issue first, so the approach can be agreed before you
   write it.
2. Work on a branch and open a pull request against `main`. Keep it to one change, say what it does and why, link
   the issue (`Closes #123`), and include a screenshot of the demo data for a visible change.
3. Update the README or the docs in the same pull request when behaviour changes.
4. CI must pass. A maintainer reviews the code, and for a front-end change also tries it at phone width (375 px)
   and in dark mode. The browser check opens every page at 375 px; for a control that only shows after a click,
   check it there with `phone_fits` in `tests/browser_check.py`. Expect questions and requests for changes; they're normal.
5. Once approved, a maintainer squash-merges it.

By contributing you agree that your contribution is licensed under the [GNU AGPL v3.0](LICENSE), like the rest of
Sanguity.
