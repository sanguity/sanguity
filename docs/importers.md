# Writing an importer

An importer reads one kind of health-record export (a portal download, a C-CDA document, a FHIR bundle, a CSV,
an Apple Health export...) and turns it into the records Sanguity shows. Each importer is **one Python file** in
`server/importers/`, plus **a test fixture**. Nothing else needs to change: the registry finds the file, and the
tests find the fixture.

[`server/importers/sanguity.py`](../server/importers/sanguity.py) is the smallest working importer (it reads
Sanguity's own data.json and exports). Copy it to start.

## The contract

```python
# server/importers/myportal.py
NAME = "myportal"              # short id: lower-case letters, digits and dashes, unique among the importers
LABEL = "MyPortal export"      # shown to whoever is importing

def detect(files) -> float:    # 0.0 = not this format ... 1.0 = certain
    ...

def parse(files) -> dict:      # {"data": {...}, "warnings": ["...", ...]}
    ...
```

`NAME` matches `^[a-z][a-z0-9-]*$` (`"csv"`, `"apple-health"`) and names your importer everywhere: in source
keys, in the fixture folder, in `get(name)`. The file name is free (`csv_labs.py` can have `NAME = "csv"`). Two
modules with the same `NAME` are both left out, and the error is logged.

### `files`

What was uploaded, unpacked: a dict of **relative POSIX path inside the upload → absolute path on disk**.

```python
{"export/data.json": "/data/uploads/41/export/data.json",
 "export/documents/3/report.pdf": "/data/uploads/41/export/documents/3/report.pdf"}
```

Paths use `/` on every system and never start with `/` or contain `..`. Look files up by the key and open
them by the value. Never open anything that isn't in `files`. `server.importers.files_in(folder)` builds the dict
for a folder, which is handy in tests and when trying an importer by hand.

### `detect(files)`

Returns how sure you are that this upload is your format, from 0.0 to 1.0. It runs for every importer on every
upload, so it must be quick:

- read only what you need: file names first, then the first few KB of a likely file. Never parse a whole file
  here, and never read files you don't need;
- return 0.0 the moment it's clearly not yours;
- don't raise. A detect that raises scores 0.0 (and is logged), but a quiet 0.0 is better.

Rough scale, so that scores from different importers compare fairly:

| Score | Means | Example |
|---|---|---|
| 1.0 | A signature only your format has | A README that names the exporting tool; a namespace plus a document type id |
| 0.8 to 0.9 | The structure is clearly yours | The expected top-level keys or root element |
| 0.4 to 0.6 | Plausible, but other formats look similar | A CSV whose header has a date column and a value column |
| below 0.2 | Not a match | |

The registry picks the highest score at or above `THRESHOLD` (0.2). If every importer returns 0 (or less than
the threshold), nothing matched and the person is told which formats Sanguity can read. On a tie the importer
whose `NAME` sorts first wins, and a warning says which other formats the files also looked like. So if your
format is a more specific case of another's (a portal's CSV variant, say), score a little higher than the general
one when you see what makes it specific.

### `parse(files)`

Returns `{"data": <records>, "warnings": [str, ...]}`.

- **`data`** is the shape build.py writes to data.json and the app loads (below).
- **`warnings`** are short sentences for the person importing: what was skipped and why, such as "3 results had no
  date and were left out." Don't list every item; count them. An empty list is fine.
- For input you can't read at all, **raise `ValueError`** with a plain-language message, such as "This file isn't
  a FHIR bundle: it has no "resourceType"." It's shown as it is, so don't put stack traces, file-system paths
  outside the upload, or record contents in it.
- Anything else you raise is a bug: the import fails with a generic error and the traceback goes to the log.

`server.importers.run(files)` picks the importer, calls `parse`, checks the result with `check()` and refuses
records the app couldn't load, so a mistake in an importer doesn't reach anyone's profile.

## The records (`data`)

The same object build.py and `demo/generate.py` write as data.json. Required keys: `events`, `details`, `labs`,
`costs` (empty is fine). Optional: `upcoming`, `problems`, `milestones`, `clinicWeights`, `outOfPocket`, `watch`. Run
`python3 -I demo/generate.py /tmp/demo` and look at `/tmp/demo/data.json` for a full, fictional example.

### `events`: the timeline

```json
{"id": 12, "t": "2026-03-02T08:30:00-06:00", "k": "visit", "title": "Follow-up visit",
 "sub": "Dr. Avery Lin · Riverbend Cancer Center", "end": "2026-03-02T09:10:00-06:00", "abn": 1, "hidden": 1, "src": "RCC"}
```

| Field | |
|---|---|
| `id` | A number unique within this import. `details` and `costs` refer to it. |
| `t` | When, as ISO 8601. Keep the source's offset when it has one; a bare date becomes noon (`common.iso`). |
| `k` | Kind: `lab` (one draw: a day's results), `labitem` (one result in it, with `hidden: 1`), `imaging`, `study`, `procedure`, `immunization` (a vaccine given, #179), `visit`, `stay` (hospital stay), `med`, `document`, `letter`, `message`, `appt_msg`, `auto_msg`, `claim`, `rx` (pharmacy fill). |
| `title`, `sub` | The line shown, and the smaller one under it (who, where, the source). |
| `end` | Optional end time, for visits, stays and conversations that span time. |
| `abn` | Optional count of abnormal results, which marks the event. |
| `hidden` | `1` for events shown only through another (a `labitem` through its `lab` day). |
| `src` | Optional short tag of the source, also used on lab points. |
| `key` | Optional stable source id, such as `"fhir:Observation/abc123"`: see [Source keys](#source-keys). |

### `details`: what opens when you click an event

An object keyed by event id (as a string in JSON). The records page loads an event's details when it's opened
(#217), so what it should list, count or search without opening one belongs in the event (`title`, `sub`, `abn`); a
lab day's `ids` are the exception, sent with the records. (A lab day's out-of-range count is worked out from its
results in `labs` by the one flag rule, #782; its `abn` counts only where none of its results is a number.) Each has a `type`:

| `type` | Fields |
|---|---|
| `file` | **A document.** `url`: its path **inside the upload** (a key of `files`, optionally with `#page=2`); `ext`: `pdf`, `png`, `html`, ... |
| `result` | `order`, `name`, `collected`, `resulted`, `provider`, `status`, `specimen`, `lab`, `dx` (list), `rows` (`[name, value, unit, range, flag]`), `report` (`[part, text, isHtml]`), `comments`. The server adds `series` when it puts the records together, never an importer: each row's lab series by name, or `null` (#782, `records._row_series`), so the page reads a row's flag from that result as Labs shows it |
| `labday` | `ids`: the `labitem` events of that draw |
| `visit` | `who`, `where`, `dx`, `procs` (list), `note`, `src` |
| `stay` | `admit`, `discharge`, `where`, `how` (where the dates came from) |
| `med` | `instructions`, `status`, `src`; when the record says: `end` (when it stopped), `prescriber`, `rxnorm` (its RxNorm code). The medication timeline (#50, `server/medications.py`) lays each medication's listings out as spans from these. On an `rx` event (a pharmacy fill with no claim, such as a FHIR MedicationDispense, #179) also `pharmacy`, `daysSupply` (a number) and `quantity` |
| `immunization` | **A vaccine given** (#179): `vaccine`, `src`, and what the record gives of `code` and `system` (`"CVX"` where it's a CVX code), `dose` (the dose number as recorded: `"2"`, or `"2 of 3"` with the series' doses), `amount` (`"0.5 mL"`), `route`, `site`, `lot`, `manufacturer`, `performer` (who gave it), `where`, `status`, `reason`, `note`. Build it with `common.immunization(vaccine, src, **fields)`, which gives the title, the sub line and this detail, so every source's look the same |
| `conversation` | `subject`, `messages`: `[{"t", "from", "body", "att"}]` |
| `letter` | `html` |
| `claim` | `insurer`, `kind` (`Medical`/`Pharmacy`), `provider`, `claimId`, `billed`, `paid`, `owe`, `status`, `lines`, `visit` |

Point `url` at the file as it sits in the upload; the app stores the documents with the records and rewrites the
links. A document the export mentions but didn't include gets `"url": null` and a warning.

### `labs`: series for the Labs charts

```json
{"name": "Hemoglobin", "unit": "g/dL", "panel": "CBC", "n": 2, "loinc": "718-7",
 "pts": [["2026-03-02T08:30:00", 11.2, 12, 16, "L", "11.2"], ["2026-04-01T08:00:00", 12.4, 12, 16, "", "12.4", "RCC"]],
 "txt": [["2026-03-02T08:30:00", "No growth"]]}
```

`pts` are `[time, number, low, high, flag ("H", "L", "A" or ""), value as reported, optional source tag, optional
edge]`, sorted by time; `n` is how many. Work the flag out with `common.report_flag(printed, value, low, high,
comparator, edge)`, the one rule every importer uses (#817): "H" or "L" as printed; a flag the report prints without
saying which way ("A", "AA", "Abnormal", "Critical", or a "Yes" in an "Abnormal" column, #835) placed by the range
(`common.range_flag`), and kept as `"A"` where the range doesn't place it, a report's own flag first (#752); none
printed (or "N", "Normal", "No"), the range's. `common.flag_code(printed)` gives the same flag as HL7's interpretation
code, as the FHIR export writes it. A flag stored before this rule, as its report printed it (an older import's "N",
">", "HH", "Normal"), is read by the same rule wherever it's shown, the page's and the server's alike
(`server/ranges.py read_flag`, `www/js/ranges.js readFlag`, #839), so it reads as a new import of it would; nothing
stored is changed. The edge is for a one-sided range whose limit leaves its edge out, as printed: `"<"` for `"<5"` (5 is above it), `">"` for
`">60"` (60 is below it); leave it off (or `""`) for a range that keeps it (`"<=5"`, `"< OR = 5"`, `">=60"`) or one
with a low and a high, and put `null` in the tag's place when there's no tag. `common.range_edge(printed, low, high)`
works it out, and the flag rule honours it (#780); a range given as text ("<5", "<=5", "=>60", "< OR = 5", "less than
or equal to 5", "3.5-5.1 mmol/L", "5,0 - 10,0") is read by `common.range_text(text, decimal_comma, whole)`, the one
parser of a range as text, every importer's, Type them in's and the FHIR export's: `(low, high, edge)` (#827, #850).
Pass `decimal_comma` where the file says whether it writes decimal commas (a spreadsheet's: `"1,250"` is 1.25 with
them, 1250 without); left out, a comma that can't be a thousands separator is a decimal one (`"3,5"`, `"0,500"`) and
one that can (`"1,250"`) isn't read. No thousands follow a leading 0 (#859): where a file's commas group thousands,
`"0,500"` isn't read, and the CSV importer keeps it as printed and says so in the upload's warnings. `whole=True`
reads a cell that is the range and nothing else (a PDF report's); a layout's cell holding a range and a unit is split
by `common.range_part`. Don't write a parser of your own. `txt` holds results that aren't numbers. Name series with
`common.canonical_lab(name, loinc)` so the same test from two sources lands on one chart (it returns None for labs it
doesn't know: keep the source's name then), and add a LOINC code when you have one. A series may carry a `key` too,
such as `"loinc:718-7"` (see below).

### Source keys

Events and lab series may carry `"key"`: a string that names the record in the source. A later import of the same
source can then update records in place instead of adding them again, and the import preview can spot duplicates.
It's optional, but give one whenever you can.

- **Stable.** Parsing the same source data again gives the same key (corrections made in the app find their event
  by it, so a key that changes loses them), whatever else is in the upload and in
  whatever order it comes. Never use random values, counters or the input's order.
- **Unique** within one importer's output: no two events share a key, and no two series do. Events and series are
  separate, so one key may appear in each.
- **Prefixed with your `NAME`** for events: `"fhir:Observation/abc123"`, `"ccda:<id root>^<extension>"`.
- **No id in the source?** Use `"<NAME>:h:"` plus the first 16 hex characters of a SHA-256 over the normalised fields
  that identify the record (date, kind, title, value...):

  ```python
  import hashlib
  def hkey(*fields):
      return NAME + ":h:" + hashlib.sha256("\x1f".join(str(f).strip().lower() for f in fields).encode()).hexdigest()[:16]
  ```

- **One event for several records** (a `labitem` for a panel's results, a `lab` day for its draws): the event takes
  the key of the source record that groups them, such as the FHIR DiagnosticReport behind a `labitem`. Where nothing
  in the source groups them, use a key scoped to the day: `"<NAME>:labitem:<YYYY-MM-DD>"` (with `"|<org>"` added
  when one upload has several organisations' results on the same day) and `"<NAME>:labday:<YYYY-MM-DD>"` for the
  `lab` day event.
- **Several events from one record** (the attachments of one report or document): the first event gets the record's
  plain key, and each of the others adds `#<part>/<i>`, naming the part of the record it came from and its position
  there: `"fhir:DiagnosticReport/1"`, `"fhir:DiagnosticReport/1#presentedForm/1"`, `"fhir:DiagnosticReport/1#presentedForm/2"`.
- **Lab series:** `"loinc:<code>"` when there's a LOINC code, otherwise `"<NAME>:<code system>|<code>"`. If you split
  one test code into several series because the source reports it in different units, add `"|<unit as reported>"`
  to each: `"loinc:2345-7|mg/dL"` and `"loinc:2345-7|mmol/L"`. As a last resort, if two series still share a key
  after that (two names under one code and one unit, which C-CDA documents produce), add `"|<name lower-cased and
  trimmed>"`: `"loinc:2345-7|mg/dL|glucose, fasting"`. Never number them (`#1`, `#2`) or use any other counter:
  the numbers would change with what else is in the upload.
- **Result points** (`pts`) have no key. A point is identified by its series key, its time and the value as reported.
- **`problems` and `upcoming`** entries may carry a key too, by the same rules, unique within their own list.

The app ignores fields it doesn't know and keeps an event's fields as they are, so a key costs nothing anywhere else.

**How the app uses them.** `records.store` keeps the key inside the event's or series' encrypted body, and beside it
a keyed hash of it (`key_hash`, an HMAC with a secret derived from the pepper), indexed, so the import preview finds
records it has seen before without decrypting every row. Before anything is saved, an upload's records are previewed
(`server/preview.py`): an event whose key the profile already has is a duplicate and is skipped unless the person
ticks it to keep anyway; a series adds only the points (time and value as reported) the profile doesn't have, and a
point it has already, but whose copy in the upload says something of its range or flag that no copy there says, or
whose row in the upload's lab day is new, is added as a copy, read as one result with the copy there (#846), always
with its own row, whose labitem and lab day are kept with it even where their keys are known (#868: the same rule for
Type them in and a sync; a copy the upload gives no row of its own is left out, never a result without its lab day).
A point its source sends again saying something else of its range or flag (#869: its row in a labitem whose key, the
source's own record's and not a `:h:` hash, is the one its point here has its row in, the same series key, time and
value) is that result, updated in place with its row, never a copy: so give a labitem the key of the report it comes
in, or of its day, as the FHIR importer does. A
record without a key falls back to the same importer, date, kind and content, which is slower and easier to fool, so
give keys. A lab day stays while any of its results is new and goes when all of them are duplicates; `check()` runs
again on what's left before it's saved.
A series whose name `lab-aliases.json` doesn't know is offered an existing series at the preview (`server/labmap.py`:
by LOINC code, then by the same name written another way, specimen included, with a compatible unit; a similar name
is only offered, never filled in, #546, #569), and the person's choice is remembered for later
imports. Only the series' `name` changes; its key stays, so name a series however the source does and key it by LOINC
code (`"loinc:<code>"`, or a `loinc` field) when the source gives one.
Keys only recognise a record coming back from the same source. The same lab result from two sources (a file, then a
connected source's sync) is spotted by `server/crossmatch.py` instead: the same LOINC code (or canonical series), the
same collection time to the minute (any time that day when one side has only a date) and the same value once units
are made comparable by exact conversions only. It is flagged at the preview and in the result's details, never merged
by itself; the person's "same result" shows it once, with both sources. For it to work, give each point the time as
the source wrote it (a bare date as noon, `common.iso`), the value as reported in `pts[5]`, and the series' unit.
`check()` refuses a key that isn't a non-empty string, or that is used twice within one import: among the events,
among the lab series, among `problems` and among `upcoming` (each list separately). It doesn't check the prefix: the
`sanguity` importer passes keys through unchanged, so a re-imported export keeps the keys the original importer gave,
and the Apple Health importer reads an export's Health Records (`clinical-records/`) with the FHIR importer, keeping
its `fhir:` keys, so the same records uploaded on their own are found as duplicates (#165).

### `costs`: one row per claim

`{"id", "d" (date), "ins", "billed", "paid", "owe", "status", "kind" ("medical"/"pharmacy"), "cat", "prov", "title"}`,
where `id` is the claim's event.


The claims importers (`claims_uhc.py`, `claims_bcbs.py`, `claims_csv.py`) share `_claims.py`: build.py's amounts,
care categories and drug names. A claim's detail also carries `visit`, a hint for linking it to a visit or stay
from another import: `{"date", "end" (when it spans days), "provider", "facility": true (a facility bill)}`. Within
one import, a facility bill inside one of that import's stays is filed under "Hospital stay (facility)", as build.py
does; a claims-only upload has no stays, so linking across imports (and re-filing those bills) is left to a later
step that reads the hint. build.py is one such step: it re-files them against the stays from all its sources.

**Claims as FHIR (#92).** The FHIR importer reads CARIN Blue Button `ExplanationOfBenefit` resources, what insurers'
patient-access APIs and Medicare Blue Button 2.0 give (in a Bundle, a bulk export's NDJSON, or a sync's pull), into
the same records (`server/importers/_fhir_claims.py`, which uses `_claims.py`'s care categories and drug names):
a `claim` event per medical EOB and an `rx` event per pharmacy one, keyed `fhir:ExplanationOfBenefit/<id>`, with the
claim detail above (plus `plan`, `network`, and for fills `drug`, `ndc`, `daysSupply`) and a Costs row. Amounts
come from the EOB's `total`, else its items' `adjudication`, by category code: billed = `submitted`, plan paid =
`benefit` (else `paidtoprovider` + `paidtopatient`, else `payment.amount`), your share = `memberliability` (else
`copay` + `deductible` + `coinsurance`). Institutional lines carry their revenue code padded to five digits, as
UHC's do, so facility bills are recognised the same way, and one inside a stay from the same import (an Encounter)
is "Hospital stay (facility)". Status is `Denied` (the whole claim; denied lines are listed on the line), `Pending`,
`Paid` or `Processed`. A claim that an adjusted claim replaces (`related` "prior", or "replacedby") is kept hidden,
with status `Replaced` and amounts of 0 (its own under `original`), so nothing counts twice, and a sync that brings
the adjustment later marks the one it saved before. Coverage only names the insurer and plan; cancelled and
entered-in-error EOBs are left out with a warning.

**Home readings and Sanguity's own FHIR export (#205).** An `Observation` the Patient measured (its performer the
Patient) of blood pressure, body temperature, blood oxygen or glucose is a home reading: the FHIR importer puts it in
`watch` (`bp_readings`, `temp_readings`, `spo2_readings`, `glucose_readings`), with the device that took it
(`Observation.device`) as its source, and one with no device as typed in. It also reads what Sanguity's FHIR export
writes in codes of its own ([docs/fhir-export.md](fhir-export.md)): the phone's daily measures into `watch` metrics,
ECGs (the waveform's `SampledData` as `ecg_waves`) and irregular rhythm notifications into `ecg`, `Communication`s into
`message` events (one per conversation, by `inResponseTo`), a `DocumentReference`'s record kind, a `MedicationStatement`'s
prescriber from the `MedicationRequest` it's based on, a visit's note from its `Encounter`'s narrative, and a decimal's
precision as written ("2.0" stays "2.0"). Cycle records in such an export are left out, with a warning.

### PDF lab reports: layouts

`pdf_labs.py` (`NAME = "pdf-labs"`) reads the lab reports that Quest's and Labcorp's patient portals give as PDFs.
It takes an upload of PDFs only (a PDF that came with other files is another export's document) and extracts each
page's text with [pypdf](https://pypi.org/project/pypdf/), in a separate Python process with a size cap
(`MAX_BYTES`), a page cap (`MAX_PAGES`), a time limit (`TIMEOUT`) and, where the system has one, a memory limit.
There's no OCR: a scan with no text layer is refused with a message saying so.

Each lab's layout is a small module in [`server/importers/pdf_layouts/`](../server/importers/pdf_layouts/) (`quest.py`,
`labcorp.py`): `recognise(lines)` says whether page 1 is that lab's report, regexes find the table's column
headings (`HEADER`), the collection date, ordering provider and specimen number above it, panel headings, and lines to
skip; and `row(cells)` reads one table line, split into cells at gaps of two or more spaces, into its name, value,
flag, unit and range, or returns None. **Never guess:** a line in the table that `row` doesn't read is a warning
naming its page and line, and a PDF no layout recognises (or two do) is refused. The package's docstring has the
contract, and its helpers (`is_value`, `is_unit`, `is_range`, `range_and_unit`) read the parts most layouts share; a
row's range is read by `common.range_text(range, whole=True)`, the one parser of a range as text (#850).

To add a layout:

1. Copy `quest.py` or `labcorp.py` to `pdf_layouts/<lab>.py` and change the names, regexes and `row`.
2. Add a function for a fictional report in that layout to `tests/fixtures/importers/pdf-labs/generate.py`, write it
   to `<lab>/<lab>-report.pdf` from `main()`, and run `python3 -I tests/fixtures/importers/pdf-labs/generate.py`.
3. Write `<lab>.expected.json` from the importer's output once you've checked every row against the PDF (the fixture
   test and `tests/test_importer_pdf_labs.py` check that every layout has one).

### The optional keys

| Key | Shape |
|---|---|
| `upcoming` | `[{"t": "2026-12-01", "title", "who", "where", "dx", "procs"}]`: future appointments |
| `problems` | `[{"name", "type", "date", "status", "src"}]`: the problem list. An allergy or intolerance (#179) has type `"Allergy"`, name `"Allergy: <substance> (<reactions>)"` and, as recorded, `substance`, `reactions` (a list), `severity`, `criticality`, `code` and `system`: `common.allergy()` builds it, as the C-CDA and FHIR importers do |
| `milestones` | `[{"date", "label", "primary": true}]`: named dates for chart markers. Since #39 they are a profile setting, changed on the Settings page; an import's are kept but not shown (an older export loaded back in whole brings them into its settings) |
| `clinicWeights` | `[[time, pounds, where]]`: weights measured at visits, charted with the scale's |
| `outOfPocket` | `{"payments": [{"d", "payee", "group", "amount", "account", "memo", "est", "key"?, "hsa"?, "receipt"?, "claim"?}], "exported", "range", "excluded", "balance"}`. Optional per payment (#55): `key` a source key, `hsa` `"HSA"` or `"FSA"`, `receipt` the key of a document event holding its receipt, `claim` the key of the claim event it paid. The served records add `oop` (server/oop.py): where each payment came from, its HSA/FSA tag, the claim it was matched to and what is counted; an import's own `oop` is ignored |
| `watch` | `{"metrics": [{"key", "name", "unit", "group", "pts": [[day, value]]}], "exercise": [{"key", "start", "end", "name", "kind", "minutes"}]}`: daily wearable data in the shape `watch/receiver.py` writes. It isn't kept with the records: an import saves it with `watch_store.save_import`, as that import's own (so undo can take it away), merged day by day with what the profile already has (see the Apple Health importer). Some differ: `glucose_meter`'s points are `[local time, value]`, a meter's readings each on its own (`server/glucose.py`, #181); `glucose_dist`'s and `glucose_hourly`'s values are lists, a CGM day's readings counted by whole mg/dL (#234) and by hour of the day with their percentiles (#303), made by `glucose.summary` as the receiver's are. It may also hold `"ecg"`, a watch's ECG recordings and irregular rhythm notifications, and `"ecg_waves"`, `{key: waveform}` with their samples, which `save_import` keeps as files of their own (`server/ecg.py`, #182). Readings one by one are lists of rows, `bp_readings` `[time, systolic, diastolic, pulse, source, app]`, `temp_readings` `[time, °C, source, app]`, `spo2_readings` `[time, %, source, app]` and `glucose_readings` `[time, mg/dL, source, app, meal]` (the phone's from `watch/receiver.py`; typed in at home with the app `"Typed in"`, `server/manual_items.py`, #224), merged across imports by minute, source and app. A metric's `typed`, `[day]`, lists the days whose value was typed in on Sources (an import of origin `manual`, #783): `watch_store.rebuild` marks them and a merge keeps the mark with the day's value, so a weight typed in is never left out as a scale's (`measures.scale_left_out`, the page's `weightsOut`) and is said as typed in. The receiver's summary may also hold `med_doses`, `[time with its offset, medication, "taken" | "skipped", dosage, units, app]`: Apple Health's Medications doses from Health Auto Export (`server/med_log.py`, #235) |

## Shared helpers: `server/importers/common.py`

The pieces build.py uses, as plain functions (standard library only):

| Helper | |
|---|---|
| `iso(s)`, `ymd(s)`, `num(s)`, `strip_html(h)` | Dates to ISO, `YYYY-MM-DD` from the start of a string, the number in `"<0.5"`, HTML to text |
| `canonical_lab(name=None, loinc=None)` | The series name from [`lab-aliases.json`](../lab-aliases.json), by LOINC code or by name/alias |
| `loinc_of(canon)`, `lab_defs()`, `lab_alias()` | The LOINC code for a series; the alias table itself |
| `unit_norm(u)`, `unit_for(canon, u)`, `unit_key(u)` | Units in a comparable form ("x10E3/uL" = "10*3/uL" = "K/uL"; mEq/L = mmol/L for sodium and friends) |
| `resolve_name(name, unit, org, labs)` | The series to add a result to: the canonical one when units agree, `"<name> (<org>)"` when they don't |
| `stay(admit, discharge, where, org, how, exact=None)` | A hospital stay event from real admit/discharge dates |
| `inferred_stays(admissions, inpatient_ts, org)` | Hospital stays guessed from inpatient results, for sources without admit/discharge dates |
| `immunization(vaccine, src, **fields)`, `dose_text(n, of)` | An immunization's title, sub line and detail (#179); a dose number as `"2"` or `"2 of 3"` |
| `allergy(substance, reactions, severity, criticality, status, date, src, code, system)` | An allergy's entry of `problems` (#179) |

The stay helpers return `{"t", "title", "sub", "detail", "end"}`; add an `id` and `"k": "stay"`. If a helper is
missing something your format needs, add to it rather than copying it, and keep build.py's output unchanged
(`tests/test_importers.py` checks).

build.py reads each export in sources.json with its importer (`epic_mychart`, `carespace`, `claims_uhc`,
`claims_bcbs`), imported by name so that it never loads an importer needing more than the standard library, and
joins the results into one data.json: new ids in source order, document links under each source's served prefix,
no source keys (edits to a setup build find events by their content), lab series of the same name shared when the
units agree (else `"<name> (<org>)"`), facility bills re-filed into another source's stays, and transfers between
hospitals. Those modules, and `common.py`, must stay standard-library only.

## Rules

- **Standard library first.** Importers run inside the app, so the packages in requirements.txt are there (pypdf, for
  `pdf_labs.py`, is one), but a new dependency means changing requirements.in and its lock files, and it must install
  on 32-bit ARM. Keep it out of `common.py` and the registry, which build.py imports with the standard library only. Parse XML with `xml.etree.ElementTree` (its
  expat parser doesn't fetch external entities) and never with anything that does.
- **Treat every upload as hostile.** Don't open paths that aren't in `files`, don't follow paths from inside a
  file to elsewhere on disk, don't run anything from it, and stream large files rather than reading them whole.
- **Deterministic output.** The same upload gives the same records: no `datetime.now()` in the records, sort what
  you collect from dicts or folders.
- **Never put real records in the repo.** Fixtures are made up: fictional people, providers and values, like
  `demo/`. Change every name, date and number in anything derived from a real export: `tools/anonymise.py` does
  most of it ([docs/anonymising-exports.md](anonymising-exports.md)), and you check the rest by hand.

## Fixtures and tests

Put each sample upload in its own folder under `tests/fixtures/importers/<NAME>/`:

```
tests/fixtures/importers/myportal/
  minimal/                  one upload, unpacked: the files as the person would upload them
  minimal.expected.json     optional: the exact {"data": ..., "warnings": [...]} parse() should return
  with-documents/
```

`tests/test_importers.py` finds them by itself. For every fixture folder it checks that the registry picks
`<NAME>` (so your `detect` beats everyone else's on your files), that `parse` keeps to the contract, and, where
there's an `.expected.json`, that the output matches it exactly. Write an `.expected.json` by running your
importer once and reading the result carefully. Add your own `tests/test_importer_<name>.py` (unittest, like
the others) for edge cases (bad input raising `ValueError`, warnings). CI runs every `tests/test_importer*.py`,
so it needs no change for a new importer.

Fixture files of every type are committed (the `.gitignore` rules that keep out health data, such as `*.xml` and
`*.pdf`, make an exception for `tests/fixtures/`), so a fixture must be fictional.

```sh
python -m unittest discover -s tests -p "test_importer*.py" -v      # what CI runs: the registry and every importer
python -c "from server import importers as i; print(i.run(i.files_in('tests/fixtures/importers/myportal/minimal'))['warnings'])"
```

## The registry: `server/importers/__init__.py`

| Function | |
|---|---|
| `importers()` | Every importer module, by `NAME`. A module that fails to import or lacks `NAME`, `LABEL`, `detect` or `parse` is logged and left out, as is one whose `NAME` doesn't match the pattern or is shared with another module. Modules starting with `_`, and `common`, aren't importers. |
| `get(name)` | One importer by `NAME`. |
| `files_in(folder)` | The `files` dict for an unpacked upload. |
| `scores(files)` | `[(score, module)]`, best first. |
| `match(files)` | `{"importer": module or None, "score", "tied": [modules with the same score]}`. |
| `run(files, name=None)` | Parse with the named importer or the best match and check the result: `{"importer", "label", "data", "warnings"}`, or `ValueError`. |
| `check(out, files=None)` | Problems with a `parse()` result, as sentences; `[]` when it keeps to the contract. |

## Importing from the browser: `server/jobs.py`

An upload is imported as a background job, so the page stays usable while a large export is read. One worker thread
runs the jobs in turn. A job opens the upload's files (decrypted temporary copies), picks the importer with `match()`
unless the person asked for one, calls `parse()`, and `check()`s the result. It then saves everything in one
transaction: the records (`origin` "upload", `format` your `NAME`) and their document links. An import from an upload
adds to the profile's records and never replaces them.

- A `{"type": "file", "url": "<path in the upload>"}` link is indexed to the stored, sealed copy of that file in the
  upload's folder, so the document still opens after the temporary copies are deleted.
- A `ValueError` from `parse()` is shown to the person as it is, so word it for them. Anything else they see as "the
  importer stopped with an error", and its traceback goes to the app's log.
- A failure at any point rolls the transaction back. A failed import leaves no records and no documents behind, and
  its upload is removed unless another import uses it (#547).
- When no importer matches, the message lists every `LABEL` and asks for an issue with an anonymised sample.

| Route | |
|---|---|
| `POST /api/profiles/{pid}/uploads/{upload_id}/import` | Owners and editors. Optional `{"importer": NAME}`. Returns `{"id"}` (202). |
| `GET /api/profiles/{pid}/jobs/{id}` | `state` (queued, running, done, failed), `stage` (detecting, parsing, saving), `importer`, `label`, `counts`, `range`, `warnings`, `message`. |
| `GET /api/profiles/{pid}/jobs` | The profile's 20 most recent jobs, newest first. With `?state=preview` (or another state), every job in that state instead: Sources' "Waiting for you" lists the previews left open this way. |

## Import history and undo: `server/history.py`

Every import can be undone as a unit, so an importer never needs a clean-up of its own. Undo removes, in one
transaction, the import's records and documents (they cascade from its `imports` row), the upload's raw files unless
another import still uses them, and its watch data. Each import's `watch` is kept as its own sealed file
(`<profile>/watch/imports/<import id>.json`) and the merged file is rebuilt from those, so undoing one takes away
exactly the days and workouts it brought. Anything an importer writes outside its `parse()` result can't be undone:
don't. Records typed in on the Sources page (`server/routes_manual.py`, origin `manual`) are one import per save, built in
the same shape as an importer's output and undone the same way, with the uploads that hold their attached files.

| Route | |
|---|---|
| `GET /api/profiles/{pid}/imports` | Anyone the profile is shared with. `{"can_undo", "imports": [...]}`, newest first: `id`, `at`, `origin`, `label`, `by`, `files`, `counts`, `range`, `state` (active, undone), `can_undo`, `note`. |
| `POST /api/profiles/{pid}/imports/{id}/undo` | Owners and editors. A setup build import is refused (409): the next rebuild would bring it back. A command-line import is undone, but what it replaced can't come back. `?at=` (optional, the entry's `at`): another import with that id is refused (404), as is one undone already (#512; ids are never used again since schema version 37). |
