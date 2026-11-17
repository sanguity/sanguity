# Install templates

One-click installs from the self-hosting app catalogs. Every template runs the published image,
`ghcr.io/sanguity/sanguity` (built by [`release.yml`](../.github/workflows/release.yml) from a version tag), and sets the
same things [`compose.release.yaml`](../compose.release.yaml) does: the data folder, the web port (8730) and the
phone-upload port (8731), the time zone, the user and group the app runs as, and a folder for the encryption key
kept apart from the data and its backups.

| Folder | Catalog | Format | Runs |
|---|---|---|---|
| [`truenas/`](truenas/) | TrueNAS Apps (community train) | `ix-dev` app: `app.yaml`, `questions.yaml`, `templates/` | the four services |
| [`unraid/`](unraid/) | Unraid Community Applications | Docker template XML | one container (`docker/allinone.py`) |
| [`casaos/`](casaos/) | CasaOS / ZimaOS AppStore | `docker-compose.yml` with `x-casaos` | the four services |

Unraid templates describe a single container, so the Unraid one runs the image's default command, which starts
setup, the app, nginx and the receiver together in one container.

**Status:** nothing has been submitted to any catalog. Each folder's README says how. A catalog install can only
work once a version is tagged and its image published. Until then the templates point at `0.1.0` (TrueNAS,
CasaOS) or `latest` (Unraid), which don't exist yet. When tagging, set the TrueNAS and CasaOS versions to the
tag's.

**Before submitting anywhere:** make the package public. GHCR creates `ghcr.io/sanguity/sanguity` as a
private package the first time the release workflow pushes it. Change it under the organisation's Packages →
sanguity → Package settings → Change visibility, or no catalog can pull it.

## Checks

`python3 deploy/check.py` (run by the `templates` job in [`check.yml`](../.github/workflows/check.yml)):

- every file parses (YAML, XML);
- every template uses the released image;
- TrueNAS and CasaOS run the same four services as `compose.release.yaml`, with the same mounts (read-only
  where it is), container ports, commands and read-only root filesystems. One difference is allowed: TrueNAS's
  app library mounts whole datasets, so its app container has the one watch folder writable, where compose mounts
  it read-only (its summaries, and its data for backups, #227) with only `keys/` writable;
- the Unraid container mounts every folder those services do, publishes 8080→8730 and 8081→8731, and sets
  PUID, PGID and TZ;
- the key folder isn't inside the data folder;
- the catalogs' own rules: TrueNAS `app.yaml` fields and `questions.yaml` covering every value the template reads;
  CasaOS's validator rules (top-level `name`, reverse-domain `x-casaos.id`, `docker compose config`) and its
  required `x-casaos` fields.

With `--truenas-lib`, CI also renders the TrueNAS app with TrueNAS's own app library (pinned in `check.yml`) and
compares the rendered compose file. The `release-image` job runs the image both ways, as `compose.release.yaml`'s
services and as the all-in-one container, through the same smoke test as the Docker install.

What only a real install can show: that each catalog's UI accepts the template and the app comes up to its setup
page. That needs the published image, so it can be checked after the first tag.
