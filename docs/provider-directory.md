# Find your provider: the endpoint directory (#93)

People know their hospital's or insurer's name, not its FHIR base URL. **Find your hospital, clinic or insurer**, in
the connect step of **Sources → Portals and health plans** (#166; `providers.js`, and `providers.html` opens it),
searches a directory of endpoints and shows, for the one chosen, its vendor, how realistic connecting it is under the
decision in [connected-sources-oauth.md](connected-sources-oauth.md) (option 1: each install registers its own app
with each vendor), and whether it can be connected here: a source set up on this server with the same FHIR base is
connected from there; otherwise an admin is taken to **Settings → Connected-source apps** with the FHIR base filled in,
where each vendor's setup steps are (`connections.GUIDES`, their one copy), and everyone else is told what to ask
their admin for.

## Where the list comes from

| Source | In the release? | Why |
|---|---|---|
| Epic's [User-access Brands bundle](https://open.epic.com/MyApps/Endpoints) (R4) | No | open.epic's [Terms of Use](https://open.epic.com/Home/TermsOfUse) allow keeping a copy for yourself and sharing only by linking to it. |
| Oracle Health's [Millennium patient R4 endpoints](https://github.com/oracle-samples/ignite-endpoints) | No | Copyright Oracle, with no licence granted. |
| Medicare Blue Button 2.0 (sandbox and production) | Yes | A US government work. |
| Epic's, Oracle Health's and Blue Button's sandboxes | Yes | Single base URLs from each vendor's documentation. |

CMS publishes no single list of payers' patient-access endpoints; each payer publishes its own (#92).

So a fresh install finds only the sandboxes and Medicare (**Settings → Provider directory** shows which lists a server has). To add Epic's and Oracle Health's lists, an admin runs,
from the Sanguity folder on the host (Python 3.9 or later, nothing to install):

```bash
python3 tools/build_directory.py
```

It downloads the two lists (about 100 MB from Epic, 4 MB from Oracle) and writes
`data/directory/providers.json.gz` (or under `$DATA_DIR` if set in the environment): about 2,600 entries, 1.1 MB
gzipped, in November 2026. The app reads it on the next search; no restart. Run it again now and then (Epic suggests
weekly) to pick up new hospitals. This is the only part that downloads anything, and only when you run it.

`server/directory/providers.json` is the bundled file. Maintainers regenerate it with
`python3 tools/build_directory.py --bundled`, which uses no network; `tests/test_directory.py` checks it matches.

## Known to work, from people's reports

This is where health systems and plans that someone has connected with their own install's app go (#165), so the
next person knows what to expect before registering one. There are no reports yet: the project has registered no
app with any vendor, and the tests use the fake FHIR server.

| Health system or plan | Vendor | Sandbox or production | Reported | What it took |
|---|---|---|---|---|
| *(none yet)* | | | | |

To add one, open an issue (or a pull request to this table) saying which health system or plan, its vendor, whether
it was the sandbox or real records, roughly when, and what the registration and any review involved. Never include
records, client IDs, secrets or your install's address. The directory's data has no field for this yet; once there
are reports, they can be carried into `server/directory/providers.json` beside each entry.

## What the app does and doesn't do

- **No lookups.** Searching reads the files above; the server contacts no one. Logos and portal addresses a list
  carries are passed on as published, never fetched; the page shows a logo only when the file holds it as a
  `data:` image.
- **Search** matches each word of the query against names, location names, the health system, and city or state
  (a state by its abbreviation or name): exactly, as the start of a word, or with one typing mistake in words of
  four to six letters and two in longer ones.
- **API** (any signed-in account; public data): `GET /api/directory?q=…` and `GET /api/directory/entry?id=…`.
  Each entry has `id`, `name`, `vendor`, `kind`, `fhir_base`, `fhir_version`, `org`, `places`, `aliases`, `logo`,
  `portal`, `source` and `retrieved`. The answer also has `vendors` (developer site, scopes, whether a client
  secret is needed, and whether the vendor is realistic for a home install) and `return_addresses` (each of
  `PUBLIC_ORIGINS`, and the address the page was opened at, plus `/connect/return`, with whether vendors are likely
  to accept it). See `server/routes_directory.py`.
