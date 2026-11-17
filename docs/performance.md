# Performance budgets

The demo is two years of one person's records. A real install keeps much more: a ten-year Apple Health export, years
of portal records, thousands of claims. These budgets say how big and how slow Sanguity may get with records like
that, and [`tests/test_performance.py`](../tests/test_performance.py) fails when one is missed. CI runs it on every
pull request that can affect the app (#201).

## The big fixture

`python3 -I demo/generate.py <folder> --end 2026-11-15 --big` writes the demo with eight more years before its
story: routine care every couple of weeks with more tests than the story's, three prescriptions filled every month,
other care, portal messages and scanned documents; and readings from home (#180, #184): blood pressure a few
mornings a week, a thermometer during colds, a watch's blood oxygen each night for the last three years, with the
same taken at the clinic's visits. It is fictional and the same on every run, like the demo.

| | The demo | `--big` |
|---|---:|---:|
| Years | 2 | 10 |
| Events | 307 | 5,450 |
| Lab results (tests) | 442 (12) | 5,048 (47) |
| Claims | 96 | 2,000 |
| Documents (a small PDF each) | 1 | 1,001 |
| Payments | 96 | 1,333 |
| Watch and scale days | 20 months | 10 years |
| Blood glucose (#181) | none | 3 years of meter readings, 336 CGM days |
| Workouts | 115 | 1,026 |
| Home blood pressure and temperature readings | – | 1,381 and 180 |

Blood glucose (#181) reaches the page as days, never as a CGM's five-minute readings, so the fixture has the days and
the meter's readings, in the receiver's shape: 77 KB of the watch data and about 800 elements on Body and activity (a
dot a meter reading, two paths a sensor's run of days). The readings themselves stay in the receiver's own database;
what a year of them costs there is in [docs/database.md](database.md#the-watch-receivers-own-database).

The test loads it as the setup build into a temporary data folder of its own and serves it with the real backend,
as `tests/browser_check.py` does with the demo. It also makes two more large inputs: a ten-year Apple Health
`export.xml` (15 MB, about 65,000 records) and a patient on the fake SMART on FHIR server
([`tests/fakefhir`](../tests/fakefhir/README.md)) with 5,000 lab results on 500 days, 500 visits, 1,000 claims and
100 documents.

## The budgets

They are in one place, `BUDGETS` at the top of `tests/test_performance.py`; this table explains them. A budget that
changes changes in both.

Sizes and element counts are exact, so their budgets are close to what was measured: they catch the records page
sending something twice, or a list that stops being cut short. Times are the median of three runs and their
budgets are about ten times what a developer's machine measures, so CI's slower, shared machines pass while a
regression like the one fixed below (seconds instead of a tenth of one) still fails.

| Budget | What is measured | Measured | Budget |
|---|---|---:|---:|
| `records_bytes` | The records page's data, `GET /api/profiles/{id}/data`, as JSON | 3.50 MB | 4.5 MB |
| `records_gzip_bytes` | The same as nginx sends it (gzip at its default level) | 439 KB | 600 KB |
| `records_cold_s` | That request right after the records changed, put together from the database | 0.28 s | 2.5 s |
| `records_s` | That request again with nothing changed (the server keeps the answer) | 0.003 s | 0.25 s |
| `watch_bytes`, `watch_s` | `GET /api/profiles/{id}/watch`: ten years of watch and scale days, with the glucose and the home readings | 1.28 MB, 0.03 s | 1.5 MB, 0.5 s |
| `build_load_s` | The big build loaded into a profile (the first start, `setup rebuild`) | 0.19 s | 5 s |
| `export_s` | **Export everything** of the big profile: a 1.4 MB zip with its 1,000 documents | 0.55 s | 6 s |
| `import_s` | That zip imported into an empty profile through the background job: uploaded, previewed, saved | 0.86 s | 10 s |
| `apple_health_s` | The ten-year Apple Health `export.xml` imported the same way | 0.48 s | 10 s |
| `sync_s` | A connected source's first pull of the big FHIR patient (186 requests), its preview saved | 1.7 s | 15 s |
| `page_ready_ms` | The records page, loaded on each tab, from the request to its `sanguity:ready` mark (`www/js/app.js`) | 132 ms (Body and activity) | 1,500 ms |
| `tab_draw_ms` | A tab's first draw with the period **All**, the page loaded on another tab, to the next frame | 110 ms (Body and activity) | 1,000 ms |
| `tab_elements` | Elements in a tab with the period **All** | 16,185 (Body and activity) | 18,000 |
| `phone_page_ready_ms` | `page_ready_ms` at 375 px with the CPU four times slower, like a phone's | 437 ms | 5,000 ms |
| `phone_tab_draw_ms` | `tab_draw_ms` the same way | 453 ms (Body and activity) | 3,000 ms |

Measured on a 20-thread desktop (Intel Core Ultra 7 265K), Chromium headless. Each tab's own numbers, and the size
of each part of the records data, are printed at the end of the test's output; `PERF_REPORT=file.json` also writes
them to a file.

```sh
python3 tests/test_performance.py    # about 30 seconds; needs Playwright's Chromium, like the browser check
```

## What the budgets found, and what changed (#201)

| | Before | After |
|---|---:|---:|
| Body and activity, first draw with ten years of watch data | 1,988 ms | 110 ms |
| …on a phone (CPU four times slower) | 8,018 ms | 453 ms |
| The records page loaded on Body and activity | 1,955 ms | 132 ms |
| Costs, first draw with the period **All** | 146 ms | 35 ms |
| …on a phone | 634 ms | 171 ms |
| Timeline, first draw with the period **All** | 98 ms | 63 ms |
| Elements on Costs with the period **All** | 14,479 | 3,812 |
| The big profile's own export imported back | refused ("didn't recognise these files") | 0.86 s |

- **Body and activity** looked through every weight reading for each reading to find the ones within 90 days, and
  turned each sleep-stage list into a lookup once per night instead of once (`hData` in `www/js/health.js`). Both
  grew with the square of the days: about 3,650 nights made it seconds. The readings are now put in time order and
  the nearby ones found from there, and each lookup is made once.
- **Costs** lists 200 amounts at a time under its out-of-pocket card, with **Show 200 more**, as its claims table
  already did; a payment that is open stays listed. Ten years of claims and payments were 1,724 rows drawn on every
  change.
- **Dates** are formatted with one formatter per format, kept (`_fmt` in `www/js/dates.js`), instead of a new one
  for every date shown; the timeline and Costs show thousands.
- **A big export imports again.** The Sanguity importer looked for `"events":` in the first 64 KB of `data.json`,
  but an export lists the profile-wide parts first, and ten years of payments are more than 64 KB. It now reads the
  file a megabyte at a time until it finds it (`server/importers/sanguity.py`).
- **The fake FHIR server** copied every matching resource for each page it served; it copies only the page now, so
  a 5,000-result pull takes 1.7 s in the test instead of 6.8 s. This is the test tool only (`tests/fakefhir`).

No index was needed: every query the records page and the imports make on a profile already uses one, and the
server's time is mostly decrypting rows (each is encrypted on its own), which grows with the records and nothing
else.

## Not done yet

- **Details on demand.** Of the 3.5 MB the records page reads, 1.4 MB is the details the drawer shows (a claim's
  lines, a result's rows), mostly for records nobody opens. Loading them when the drawer opens would make the data
  about 40% smaller. The page uses them in a few other places (units, the visit summary), so it is a change of its
  own.
- **Body and activity and the Timeline draw a mark per day or per record.** With ten years shown that is about
  14,500 and 11,000 elements: within the budget, and quick, but they would be summarised by week once many fall on
  each pixel, as the sleep and steps charts already are.
