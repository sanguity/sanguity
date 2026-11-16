# CasaOS / ZimaOS app

[`Sanguity/docker-compose.yml`](Sanguity/docker-compose.yml) is an app for the
[CasaOS AppStore](https://github.com/IceWhaleTech/CasaOS-AppStore) (also ZimaOS's), in its v2 format: a normal
compose file with one top-level `x-casaos` block. It runs the four services of
[`compose.release.yaml`](../../compose.release.yaml) on the published image. CasaOS fills in `$AppID`, `$PUID`,
`$PGID` and `$TZ`; `x-casaos.main` is `web`, opened at port 8730.

| Folder on the device | Mounted at |
|---|---|
| `/DATA/AppData/sanguity/data` | `/data` |
| `/DATA/AppData/sanguity/key` | `/run/secrets/sanguity` (the encryption key, apart from the data; read-only except for setup) |
| `/DATA/AppData/sanguity/backups` | `/backups` |
| `/DATA/AppData/sanguity/watch` | `/watch` (its `public/` summary read-only in the app) |
| `/DATA/AppData/sanguity/records` | `/srv/records`, read-only |
| `/DATA/AppData/sanguity/config` | `/config`, read-only (`sources.json`, `settings.json`, `corrections.json`) |

The setup code is in the logs of `sanguity-app`, and in `/DATA/AppData/sanguity/data/setup-code.txt`.

## Before the AppStore: install it as a custom app

In CasaOS, App Store → Custom Install → Import, paste `docker-compose.yml`, and install. CasaOS fills in the
variables as it would for a store app.

## Submitting it to the AppStore

1. Tag a release first (the image must exist), and make the GHCR package public (see [`../README.md`](../README.md)).
   Set the image tags and `x-casaos.version` to the release.
2. Fork [IceWhaleTech/CasaOS-AppStore](https://github.com/IceWhaleTech/CasaOS-AppStore) and copy `Sanguity/` to
   `Apps/Sanguity/`. Add `icon.png`, `thumbnail.png` and `screenshot-1.png`… beside it, as the other apps do, and
   point `icon`, `thumbnail` and `screenshot_link` at their `cdn.jsdelivr.net/gh/IceWhaleTech/CasaOS-AppStore@main/Apps/Sanguity/`
   URLs. Their docs (`docs/specs/compose-and-x-casaos.md`, `docs/specs/assets-and-i18n.md`) give the sizes.
3. Run their build locally: `./scripts/build_dist.sh`. Their PR checks run the same build, plus
   `.github/actions/validate-compose`: the top-level `name`, `x-casaos.id`, and `docker compose config -q`.
4. Open a pull request there, as their `CONTRIBUTING.md` describes. Say it's a new app, what it does, and how you
   checked it.
