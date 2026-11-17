# TrueNAS app

[`sanguity/`](sanguity/) is a TrueNAS app in the layout of the [TrueNAS apps catalog](https://github.com/truenas/apps)
(`ix-dev/community/<app>/`), for TrueNAS 24.10 and later:

| File | What it is |
|---|---|
| `app.yaml` | The catalog entry: name, version, the image's `app_version`, the app library version, run-as user |
| `item.yaml` | Categories, icon and screenshots for the catalog page |
| `ix_values.yaml` | The image (`ghcr.io/sanguity/sanguity:<version>`) and the container names |
| `questions.yaml` | The install form: time zone, user and group, the two ports, the six storage locations, resources |
| `templates/docker-compose.yaml` | Renders the four services with TrueNAS's app library, plus its `permissions` container |
| `templates/test_values/basic-values.yaml` | Values TrueNAS's CI renders and starts the app with |

It runs `setup`, `app`, `web` and `receiver` like [`compose.release.yaml`](../../compose.release.yaml). All four run
as the chosen user (568, `apps`, by default). TrueNAS's `permissions` container hands each storage location to
that user before setup starts, so setup doesn't need root as it does in compose. Storage defaults to ixVolumes
(datasets TrueNAS creates); each one can be a host path instead:

| Storage | Mounted at | Note |
|---|---|---|
| Data | `/data` | the database and each profile's files |
| Encryption Key | `/run/secrets/sanguity` | apart from the data; read-only except for setup. Keep a copy elsewhere |
| Backups | `/backups` | where Sanguity writes its backups, on the schedule set under **Admin → Backups** or with `python -m server.manage backup`. Copy them off this machine too |
| Watch Data | `/watch` | the receiver's data; the app reads its summary and, for backups, its data, and writes the receiver's per-profile secrets (`keys/`) |
| Record Exports | `/srv/records` | read-only; for `sources.json`; can stay empty |
| Config | `/config` | read-only; `sources.json`, `settings.json`, `corrections.json`; can stay empty |

After installing, the setup code is in the logs of the app container (Apps → Sanguity → Workloads → app → logs).

## Before the catalog: install it as a custom app

Until it's in the catalog, `compose.release.yaml` installs as a custom app: Apps → Discover → ⋮ → Install via YAML.
Replace each `${...}` with its value, or with the default after `:-`, and use absolute host paths. That's what
`docker/dev-stack.sh --truenas-app` does for a checkout.

## Submitting it to the catalog

1. Tag a release first (the image must exist), and make the GHCR package public (see [`../README.md`](../README.md)).
2. Fork [truenas/apps](https://github.com/truenas/apps) and copy `sanguity/` to `ix-dev/community/sanguity/`.
3. Set `app_version` in `app.yaml` and the tag in `ix_values.yaml` to the release, pinned by digest the way the
   other apps are (`"1.2.0@sha256:..."`, from `docker buildx imagetools inspect ghcr.io/sanguity/sanguity:1.2.0`).
   Start `version` at `1.0.0`. Set `lib_version` to the newest library in `library/`, if it changed.
4. Run their CI locally, which renders and starts the app with the test values and fills in `lib_version_hash`
   (it needs Docker):
   `python3 .github/scripts/ci.py --app sanguity --train community --test-file basic-values.yaml`.
5. Open a pull request there, as their `CONTRIBUTIONS.md` describes. TrueNAS's maintainers usually rehost the
   icon and screenshots on `media.sys.truenas.net`, and may ask for changes to fit their conventions.

Their Renovate bot then proposes new image tags as releases are published.
