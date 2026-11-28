# Unraid template

[`sanguity.xml`](sanguity.xml) is an Unraid Docker template for Community Applications (CA). An Unraid template
describes one container, so it runs the published image's default command, `docker/allinone.py`. That starts
setup, then the app, nginx and the watch receiver in the one container. The container starts as root so setup
can hand its files to `PUID:PGID` (99:100, Unraid's `nobody:users`, by default), then runs everything else as
that user. The root filesystem is read-only (`--read-only --tmpfs /tmp`), and it restarts unless stopped (`--restart unless-stopped`: Settings → General's Restart now stops it to apply a setting).

| Setting | Container | Default on the server |
|---|---|---|
| Web UI port | 8080 | 8730 |
| Watch receiver port | 8081 | 8731 |
| Watch receiver port, for the upload address (the same number again: the app gives it in the phone's upload address, #1007) | `RECEIVER_PORT` | 8731 |
| Data | `/data` | `/mnt/user/appdata/sanguity/data` |
| Encryption key | `/run/secrets/sanguity` | `/mnt/user/appdata/sanguity-key` |
| Backups (Sanguity writes them on the schedule set under **Settings → Install → Backups**; copy them off the server too) | `/backups` | `/mnt/user/appdata/sanguity/backups` |
| Watch data | `/watch` | `/mnt/user/appdata/sanguity/watch` |
| Record exports (advanced, read-only) | `/srv/records` | `/mnt/user/appdata/sanguity/records` |
| Config files (advanced, read-only) | `/config` | `/mnt/user/appdata/sanguity/config` |
| Time zone | `TZ` | `UTC` |
| PUID / PGID (advanced) | | 99 / 100 |

The key has its own appdata folder, `sanguity-key`, not one inside `sanguity/`. A backup of `appdata/sanguity`
(for example with the Appdata Backup plugin) then doesn't carry the key along with the data. Keep a copy of
the key somewhere else.

Commands from the README run with `docker exec` as the app's user, for example
`docker exec -u 99:100 Sanguity python3 -m server.manage list`. A rebuild of `sources.json` data runs as root:
`docker exec Sanguity python3 -I /app/docker/setup.py rebuild`. The setup code is in the container's log, and in
`data/setup-code.txt`.

## Before CA: install it from the template

Copy `sanguity.xml` to `/boot/config/plugins/dockerMan/templates-user/my-Sanguity.xml` on the server. Then go to
Docker → Add Container, pick **Sanguity** under Template, check the paths and click Apply.

## Submitting it to Community Applications

1. Tag a release first (the image must exist), and make the GHCR package public (see [`../README.md`](../README.md)).
   The template uses `:latest`, so it follows releases. If you change that to a version tag, bump it with each
   release.
2. CA reads templates from a GitHub repository that holds only templates. Either point CA at this repository's
   `deploy/unraid/` folder, if its maintainers accept a subfolder, or copy `sanguity.xml` to a templates
   repository of its own (e.g. `sanguity/unraid-templates`). Then update `<TemplateURL>`, which Unraid uses for
   template updates, to the file's raw URL there.
3. Open a support thread for Sanguity in the Docker Containers section of the Unraid forums and set `<Support>`
   to it. CA expects one per application.
4. Submit the repository to CA as its current policy describes (the "Community Applications" policies and
   submission thread on the Unraid forums). CA's maintainers check templates themselves; there is no lint to run
   beforehand beyond the XML parsing and the checks in `deploy/check.py`.
