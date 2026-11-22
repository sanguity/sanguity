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
`export.xml` (17 MB, about 75,000 records, home blood pressure, glucose and temperature among them since #223) and a
patient on the fake SMART on FHIR server ([`tests/fakefhir`](../tests/fakefhir/README.md)) with 5,000 lab results on 500 days, 500 visits, 1,000 claims and
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
| `records_bytes` | The records page's data, `GET /api/profiles/{id}/data`, as JSON: every record, its details apart (#217, below) | 2.11 MB | 2.6 MB |
| `records_gzip_bytes` | The same as nginx sends it (gzip at its default level) | 256 KB | 350 KB |
| `records_cold_s` | That request right after the records changed, put together from the database | 0.29 s | 2.5 s |
| `records_s` | That request again with nothing changed (the server keeps the answer) | 0.002 s | 0.25 s |
| `details_s` | A lab day's results as the drawer opens it, `GET /api/profiles/{id}/details?ids=…` (3.8 KB, #217) | 0.002 s | 0.25 s |
| `watch_bytes`, `watch_s` | `GET /api/profiles/{id}/watch`: ten years of watch and scale days, with the glucose and the home readings | 1.28 MB, 0.03 s | 1.5 MB, 0.5 s |
| `build_load_s` | The big build loaded into a profile (the first start, `setup rebuild`) | 0.19 s | 5 s |
| `export_s` | **Export everything** of the big profile: a 1.4 MB zip with its 1,000 documents | 0.55 s | 6 s |
| `fhir_export_s` | The same **as FHIR R4** (#205): a 3.7 MB zip, every record a resource, ten years of daily measures an Observation a day each | 1.6 s | 16 s |
| `import_s` | That zip imported into an empty profile through the background job: uploaded, previewed, saved | 0.86 s | 10 s |
| `apple_health_s` | The ten-year Apple Health `export.xml` imported the same way | 0.67 s | 10 s |
| `sync_s` | A connected source's first pull of the big FHIR patient (186 requests), its preview saved | 1.7 s | 15 s |
| `page_ready_ms` | The records page, loaded on each tab, from the request to its `sanguity:ready` mark (`www/js/app.js`) | 132 ms (Body and activity) | 1,500 ms |
| `tab_draw_ms` | A tab's first draw with the period **All**, the page loaded on another tab, to the next frame; and each **Show more** of the Timeline's list (#277, below) | 110 ms (Body and activity) | 1,000 ms |
| `tab_elements` | Elements in a tab with the period **All** | 10,903 (the Timeline) | 12,000 |
| `phone_page_ready_ms` | `page_ready_ms` at 375 px with the CPU four times slower, like a phone's | 437 ms | 5,000 ms |
| `phone_tab_draw_ms` | `tab_draw_ms` the same way | 453 ms (Body and activity) | 3,000 ms |
| `pan_frame_ms` | A frame of dragging a tab's charts with the period **All** (Body and activity, Labs' charts and its heatmap), from the pointer's move to the frame after it, every chart drawn again (#292, below) | 41 ms (Body and activity, the heatmap) | 150 ms |
| `phone_pan_frame_ms` | `pan_frame_ms` at 375 px with the CPU four times slower | 154 ms (the heatmap) | 250 ms |
| `share_page_px` | A share link's page with every section, of any date, at 1400 px (#491, below) | 9,812 px | 20,000 px |
| `static_bytes` | The app's stylesheets, fonts and SVG icons in `www/`, as nginx sends them (#253, below) | 281.9 KB | 310 KB |

Measured on a 20-thread desktop (Intel Core Ultra 7 265K), Chromium headless. Each tab's own numbers, and the size
of each part of the records data, are printed at the end of the test's output; `PERF_REPORT=file.json` also writes
them to a file.

```sh
python3 tests/test_performance.py    # about 30 seconds; needs Playwright's Chromium, like the browser check
```

## The static files (#253)

`static_bytes` doesn't depend on the records: it is what the redesign adds to the app's own files, so a new font,
stylesheet or icon set has to fit. It counts `www/css/*.css`, `www/fonts/*.woff2` and `www/icons/*.svg` (the icon
sprite and the app's icon) **as nginx sends them**: stylesheets and icons compressed (`gzip_types`, level 1, files of
1 KB and more), fonts as they are, since woff2 is compressed already. The install icons (PNG) are fetched only when the
app is installed.

| | As sent | On disk | What a page fetches |
|---|---:|---:|---|
| Stylesheets (tokens, base, app, summary) | 51.7 KB | 159 KB | every page: tokens, base and app, or its own `<style>` |
| Inter, Latin | 48 KB | 48 KB | every page |
| Inter, Latin Extended | 85 KB | 85 KB | only a page with a character in its range (a name with ő or ł, say) |
| Source Serif 4, Latin and Latin Extended | 51 and 42 KB | 51 and 42 KB | only a page with a title or a headline value on it, the second as above |
| SVG icons: the sprite (#257, #354) and the app's icon | 3.2 and 0.5 KB | 10.5 and 0.5 KB | the sprite: every page with an icon, once; the app's icon: none (the browser's tab) |
| All | 281.9 KB | 396.3 KB | |

The fonts are variable, so one file holds every weight: Inter's weight axis, and Source Serif 4's weight axis without
its optical size axis (#328), which made its Latin file 122 KB for titles that are all 15.5 to 28 px. The browser keeps
them for a year (`nginx.conf`, `/fonts/`) and the service worker keeps them for offline use, so they're fetched once
a device. The sprite holds only the Lucide icons the app uses: 16 in place of glyphs (#257), and 41 for the kinds of
record, the sidebars and the top bar's menus (#354), chosen small and shared where one meaning covers two places. Each
icon added is about 150 to 250 bytes on disk (`tools/build_sprite.py`).

**How the budget came to count what's sent.** Until Redesign 3 it counted bytes on disk: 390 KB, raised to 400 KB once
Redesign 3's sprite and components had used the room left for them (393.6 KB, then 396.3 KB with its review's fixes).
That left 3.7 KB for Redesigns 4 to 7. The stylesheets are 159 KB on disk but 51.7 KB as sent, because they're
commented throughout, so a budget on disk measured comments as much as rules. Since Redesign 4, at the owner's choice,
it counts what a browser downloads: 281.9 KB, with the budget at 310 KB, room for Redesigns 4 to 7 (the shared shell
and phones, the data store, page layouts, the chart kit). nginx sends the SVG sprite compressed too (it didn't before).
A second font, a heavier cut, more icons or a large stylesheet still have to be argued for.

### A cold load on a slow connection (#328)

Nothing cached and no service worker, at 1.6 Mbps with 150 ms latency (Chromium's network throttling, the median of
five loads at 1400 px; the stylesheets and scripts gzipped as nginx sends them). First paint, and when the Source
Serif file had arrived:

| | Before the fonts (#253) | #253 | Now (#328) |
|---|---:|---:|---:|
| Sign-in, first paint | 384 ms | 532 ms | 424 ms |
| Records (Overview), first paint | 812 ms | 1,088 ms | 832 ms |
| …ready (`sanguity:ready`) | 2,989 ms | 3,266 ms | 3,257 ms |
| …Source Serif arrived | – | 4,034 ms | 3,668 ms |
| Settings, first paint | 744 ms | 1,068 ms | 764 ms |
| …Source Serif arrived | – | 2,400 ms | 1,717 ms |

The first paint was late because of Inter's preload, not the font's size: a preloaded font is fetched at the same
priority as the stylesheets, so on a slow connection it shares their bandwidth and the page paints when both have
arrived. No page preloads a font now, so a page paints in the fallback within 20 to 40 ms of what it did before the
fonts, and swaps to Inter when it arrives (0.8 s on sign-in; on the records page before its records are drawn). The
fallbacks are sized to the fonts (#329, `www/css/base.css`), so the swap hardly moves anything: a layout shift of 0.001
at most on the Overview, Settings, Costs and Body and activity, at 1400 and 375 px (`tests/browser_check.py` holds the
fonts back to check it). Preloading Source Serif on the records page was measured too: it arrived at 1.3 s, before
the records, but the first paint was 116 ms later and the page ready 265 ms later (its 51 KB on the same connection),
so it isn't preloaded; it arrives about 0.4 s after the page is ready, and the headline values swap in place.

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

## The Timeline's list, all of it (#277)

The Timeline's list of events stopped at 600 rows and said to narrow the dates, which ten years of records reach. It
draws 400 events at a time now, whole days, with **Show more** for the next 400; each month of it is
`content-visibility: auto`, so the browser skips laying out and painting the months off screen. Workouts (most of a
watch-wearer's events) are left out of the list unless asked for, their lane staying, and a day's several fold into
one row when they are listed. The test draws the big fixture's list with the period **All** and presses Show more
until every event is listed, the first new row focused each time (`check_timeline`), each press within `tab_draw_ms`:

| The big fixture, period **All** | Desktop | Phone (CPU ×4) |
|---|---:|---:|
| Events listed (workouts left out) | 1,878 | 1,878 |
| Drawn at first, and presses of Show more to list them all | 400, 4 | 400, 4 |
| The slowest press | 22 ms | 102 ms |
| Timeline, first draw | 75 ms | 290 ms |
| Elements on the Timeline at first, and with everything listed | 10,896, 24,825 | |

`tab_elements` counts a tab as first drawn; with the whole list shown the Timeline has more, which is what someone
asks for by pressing Show more, and the months off screen cost no layout or paint.

## Body and activity's tables (#460)

A card's Table on Body and activity drew a row for every day of the period: on ten years Heart rate's had 3,493 rows,
and with every card on Table the tab had 63,709 elements, a redraw taking about 2 s on a phone. Each table shows its
latest 200 rows now, with **Show more** for the next 200, as Costs' list does. The test draws the tab again with every
card on Table ("health, every table" in `tab_draw_ms` and `tab_elements`):

| The big fixture, period **All**, every card on Table | Desktop | Phone (CPU ×4) |
|---|---:|---:|
| Elements | 10,260 | |
| First draw | 124 ms | 549 ms |

## A share link's page (#491)

A share link to ten years with every section the big profile offers (twelve, the four home measures among them) drew
every row: Costs and claims its 2,000 claims (58,717 px at 1400 px), each home measure 1,000 readings (about 29,500
px each), and the page was 162,286 px tall. Visits, procedures, costs and each home measure now show their latest
20 when there are more than 40, saying how many there are, with **Show all** (the first row it adds focused); Lab
results was already one table of the latest results, with each history on demand (#289). The page is 9,812 px.
`share_page_px` is that height at 1400 px; the test also presses Costs' Show all and finds every claim.

The server still sent every row, cut only on screen. Since #498 the view sends a long section's latest 20 and how many
there are, and Show all asks for that section in full (logged as a view of it): the ten-year link's view went from
877 KB to 460 KB, nearly all of it now Lab results' 5,432 results (each history is shown whole, and printed). The test
checks that the view holds no more than the latest 20 of each long section.

Printed with every history open (#509), a year's link to visits, lab results and medications is 8 Letter pages and ten
years of lab results 170; `test_share_printed` checks that no page before the last is more than a quarter empty and
that Latest results' column names head each page with its rows, never a page holding only a history.

## A record's details when it's opened (#217)

Of the 3.5 MB the records page read, 1.4 MB was each record's details (a result's rows, a claim's lines, a message,
a letter), mostly for records nobody opens. The page now asks for them when the drawer opens a record:
`GET /api/profiles/{id}/details?ids=…`, a lab day's results in one request, kept for as long as the page is open.

| The big fixture | Before | After |
|---|---:|---:|
| The records page's data, as JSON | 3.51 MB | 2.11 MB |
| …as nginx sends it | 443 KB | 256 KB |
| Its details | 1.39 MB | 15.5 KB (a lab day's list of results; of an immunization what the Overview lists) |
| Opening a lab day | nothing more | 3.8 KB, 0.002 s |

What the page reads of details without opening anything stays in its data (`LISTED` in `server/records.py`): a lab
day's list of results, which counts them, finds a day by its tests' names and lists them in the drawer, and of an
immunization the dose and where it was given, which the Overview lists. Everything else on the page that read
details did so inside the drawer: a result's rows in the chosen units (#41), the same result from another source
(#96), the sources of what was merged in (#100). The server's own readers (the visit summary and its measures,
share links, both exports, the medications' history) read the records with every record's details, as before
(`records.served`). The server keeps both, and each record's details apart, until the records change.

The offline copy (#56) keeps the details of the records opened on that device, with the revision of the records they
belong to (`X-Records-Rev`): a reload of the same records keeps them, a change starts again. A change made on the
page (#271) goes into the copy from its reply, which says the revision after it (#405): a dose, a journal entry, a
reminder or a Costs mark keeps the details kept so far, and an edit, whose reply carries the records whole, starts
them again. Keeping every record's
details instead would put back the 1.4 MB for each copy, and more than the person ever looked at on that device.

## Ten years, dragged (#292)

Dragging a chart draws every chart of the tab again in each frame, for the moved window. On ten years Body and
activity drew a mark for each day or reading, 16,408 elements: a frame took 75 ms on the desktop (about 13 frames a
second) and a third of a second on a phone. The test drags each tab's charts with the period **All** as a mouse would,
8 px a frame (`PAN_JS` in the test, in a page of its own), and times each frame to the one after it; `pan_frame_ms`
and `phone_pan_frame_ms` are the median frame of the slowest tab.

- **Decimation** (`ckDecimate` in `www/js/chartkit.js`): a series with more points than its marks have room for is
  drawn with fewer. Its points are put in columns along the time axis and each column keeps what shows at that size:
  a line its first, lowest, highest and last point of each pixel, so its path is the same to the pixel; a band its
  lowest low and highest high; dots the lowest and highest in each half a dot's width, and every reading flagged ▲ or
  ▼; bars from zero the tallest of each pixel. Body and activity's charts (`hChart`), Labs' charts (`drawChart`) and
  the heatmap on a phone (a pixel's cell holding the result furthest outside its range, where ten years' draws are
  under a pixel each) draw through it. It is still SVG, so axe, forced colours and the keyboard work as before, and the
  tables, tooltips and the keyboard's steps read every point, never the decimated ones.
- **Bars of one colour are one path** on Body and activity (sleep's stages, steps): 1,566 rectangles were three paths
  that look the same.
- **A frame of a drag is drawn at the chart's width as it was**, not measured again: each chart read its box's width
  after the one before it was drawn, so the browser laid the tab out again for each of its charts. The
  heatmap's panel names, cut to fit their column, are measured once a width (they were measured in every frame, the
  whole grid laid out to do it), and its procedures are found by day once, not among every record for each column.

| The big fixture, period **All** | Before | After |
|---|---:|---:|
| Body and activity, a frame of a drag | 75 ms | 41 ms |
| …on a phone (CPU ×4) | 333 ms | 70 ms |
| Labs' heatmap, a frame of a drag | 49 ms | 41 ms |
| …on a phone | 337 ms | 154 ms |
| Labs' charts, a frame of a drag | 17 ms | 17 ms |
| Body and activity, first draw | 153 ms | 126 ms |
| …on a phone | 711 ms | 410 ms |
| Elements on Body and activity | 16,408 | 9,417 |

Before and after were measured on one machine, a slower one than the desktop above. Frames are counted to the next one the browser paints, so a desktop's are steps of about 17 ms. On a phone the heatmap
is the slowest: its ten years are 5,012 cells, each one a result to hover and open, and only on a phone are its
columns narrower than a pixel. The Timeline's lanes still draw a mark a record (10,903 elements on **All**), within
`tab_elements`; the Timeline zooms by its own controls, not by dragging.
