# Anonymising an export before you share it

An importer is written from samples: the files a portal, insurer or app really produces. If yours doesn't import,
a sample of it is the most useful thing you can give, but it holds your health records, and an issue on GitHub is
public and stays online. So share only a copy with everything that points to you taken out.

`tools/anonymise.py` makes that copy. It changes names, identifiers, addresses, dates and notes, and keeps the
shape Sanguity reads (the folders, file names, field names, codes, units and values), so the copy still imports.
**It is a first pass, not a guarantee.** Free text in particular can't be cleaned reliably by a program: you must
look through the copy yourself before you share it. The checklist is [below](#before-you-share-check-the-copy).

## Making the copy

You need Python 3.12 or later, as for the rest of Sanguity. The tool is one file with no dependencies, so you can
download just [`tools/anonymise.py`](../tools/anonymise.py) instead of the whole repository. It never connects to
anything.

```sh
python3 anonymise.py ~/Downloads/MyChart.zip                  # writes MyChart-anonymised.zip beside it
python3 anonymise.py ~/Downloads/apple_health_export -o sample  # a folder gives a folder
python3 anonymise.py results.csv --jitter 10                   # one file gives one file; values moved too
```

Work on the export as you downloaded it; the original is never changed. A zip gives a zip, a folder a folder,
and one file one file. It then prints what it changed, what it left out, and what to look at before sharing:

```
Anonymised copy written to MyChart-anonymised.zip (41 files).
Changed:
  people's names: 23
  organisation names: 12
  identifiers: 58
  dates: 940
  ...
Look these over before you share the copy:
  - 6 piece(s) of free text were kept, cleaned of the names, numbers and dates the tool recognised. Read them:
    names it didn't recognise are still there. For example:
    test-results/..._test_results_raw.json: details.a1.results[0].orderMetadata.associatedDiagnoses[0]
```

| Option | What it does |
|---|---|
| `-o PATH` | Where to write the copy (default: beside the export, named `…-anonymised`). It must not exist yet. |
| `--jitter PERCENT` | Also moves lab results and vital signs by up to that many percent, keeping their decimals. Reference ranges stay as they are, so a High or Low flag may no longer match its value. Use it for a sample that may become a test fixture. |
| `--keep-notes` | Keeps notes, messages and letters (cleaned of the names, numbers and dates it found) instead of replacing them. Only when the importer needs what they say, and read every one. |
| `--keep-organisations` | Keeps the names of clinics, hospitals, labs and insurers, and their identifiers (organisation NPIs, the codes of their record systems). Useful when the sample is about one portal's quirks; it does say where you get care. |
| `--seed TEXT` | Makes the run repeatable: the same export and seed give the same copy. Keep the seed to yourself: with it, someone can check a guess at the original. Without it, every run picks a new random key. |

## What it changes

| In the export | In the copy |
|---|---|
| **Names of people**: you, relatives, clinicians, staff | Made-up names, the same everywhere: "Dr. Rowan Ellis" in one file and "ELLIS, ROWAN" in another become the same made-up doctor. Titles and credentials (Dr., MD, RN) stay. |
| **Names of clinics, hospitals, labs, insurers** | Their own words are made up and the kind of place kept: "Valley Reference Laboratory" becomes something like "Larchcliff Reference Laboratory". |
| **Identifiers**: record, member, claim, order and account numbers, SSNs, NPIs, resource ids | Made-up ones of the same shape (digits for digits, letters for letters, a short prefix such as `MRN` or `PX-` kept), the same wherever the original appears, so links between records still work. NPIs keep a valid check digit. Codes of fewer than four letters and digits (`e1`, `a2`) are kept: they number things within the export. |
| **Addresses, phone numbers, email addresses** | Made-up ones. State and country stay. |
| **Dates** | All moved back by the same random number of whole weeks (between six months and three years, the same for the whole run), so their order, the days between them, the weekday and the time of day stay right. Dates in every spelling it knows move: `2026-03-02`, `20260302083000-0500`, `03/02/2026`, `02.03.2026`, `Mar 2, 2026`, `2 March 2026`, `March 2026`, `March 2`. |
| **Birth dates** | 1 January of the (moved) year. The same birth date written elsewhere becomes the same. |
| **Notes and messages**: message bodies and subjects, letters, comments, clinical notes, narrative paragraphs in C-CDA, impressions and conclusions | "Text removed." (HTML keeps its tags). Text files and web pages in the export (`.txt`, `.html`) are replaced the same way, except a `.txt` that holds a table, which is read as one. |
| **Documents**: PDFs, pictures, scans; documents embedded as base64 | A placeholder of the same type: a one-page PDF saying "Document removed by anonymise.py", a one-pixel image. Other document types (Word, TIFF, HEIC...) become empty files. The file names stay (cleaned), so the records that link to them still do. |
| **Workout routes** (`.gpx` and other location tracks) | Left out: they show where you live and go. |
| **Comments inside XML files** | Left out. |
| **File and folder names** | Changed the same way as the contents, so a path in a record still points to its file. |

It keeps everything an importer works from: the folder layout, field names and XML tags, codes (LOINC, SNOMED,
CPT, ICD-10, RxNorm), test, medication and diagnosis names, units, values (unless `--jitter`), reference ranges,
flags, claim amounts and time-zone offsets. Formats it knows in detail: C-CDA (any portal), FHIR (bundles, single
resources, NDJSON, Apple Health's clinical records), Apple Health's `export.xml`, Epic MyChart and CareSpace exports,
lab and claims CSV, insurers' claims JSON, and Sanguity's own export. Other JSON, XML, CSV and text it cleans by the
same general rules, and says so in the report.

### People and organisations: how it finds them

A name is replaced where the format says it is one (a patient's or a provider's name field, a C-CDA `<name>`, a
FHIR HumanName or Reference), after a title ("Dr.", "Mrs.", "Dear"), before a credential ("MD", "RN"), after a
label ("Patient:", "Name:", "Ordered by:"), and before words such as Clinic, Hospital or Oncology. Once found, the
same word is replaced everywhere in the export, including file names and free text.

**NPIs.** A clinician's NPI identifies a person, so it is always replaced. An organisation's NPI is public, but
like the organisation's name it says where you get care, so it is replaced too, unless you pass
`--keep-organisations`, which keeps organisations' names and identifiers together.

## What it doesn't catch

Be clear about the limits: they are why the checklist below matters.

- **Free text is the weak spot.** A name mentioned only in passing ("my sister Ana drove me home", "saw Kim at the
  front desk") isn't recognised, nor is a place, an employer, a school or a rare event. That is why notes are
  replaced by default. Text it keeps (table cells in a C-CDA, short descriptions, and everything with
  `--keep-notes`) is listed in the report: read each one.
- **Numbers without a label.** Numbers of seven digits or more, and anything shaped like an SSN, a phone number or
  a code of letters and four or more digits, are replaced; so is a number after "MRN", "ID", "Member", "Account" and
  the like. A bare four- to six-digit number in a sentence is kept, unless the same number appears as an
  identifier somewhere else in the export.
- **Dates it can't read**: "the 3rd", "last Tuesday", "two weeks ago", a year on its own ("since 2010" in a
  sentence). Dates that look like dates but aren't real ones (31 April) are kept and counted in the report.
- **Ages** stay about right, since birth dates are kept to the year. An age over 89, or any age stated in a note,
  can still narrow down who you are.
- **The records themselves.** A rare diagnosis, an unusual treatment, exact times of day, or the values in a long
  series of results can identify someone together with other facts. Keep the sample small, and use `--jitter`.
- **Web addresses** are kept: a link can name your clinic's portal or carry an id in it. (The one exception is the
  server address of each Health Records entry in an Apple Health export, which is replaced with its health system's name.)
- **PDFs and scans** aren't cleaned, only replaced. An export that is only PDFs (a lab report, say) is refused:
  describe it in the issue instead (the lab, the layout, the column headings), or type a few made-up rows in the
  same layout.
- **Formats it doesn't know** are cleaned by its general rules only; the report names those files.

## Before you share: check the copy

1. **Read the report**, and open every file it lists under "Look these over".
2. **Search the copy** for what points to you: your surname and first name, family members' names, your date of
   birth written several ways (`1980-04-02`, `04/02/1980`, `19800402`, `April 2, 1980`), your street and town,
   phone number, email, and your record and member numbers. On macOS or Linux, unzip the copy and run
   `grep -ri "surname" MyChart-anonymised/`; on Windows, `findstr /s /i "surname" *` in its folder. Search a zip
   inside the copy too (Epic puts its visit records in one).
3. **Look at the file and folder names.**
4. **Keep it small.** Delete files the issue doesn't need: a few records of each kind (a lab panel, a visit, a
   claim, a document) show the format as well as years of them do.
5. **If you're not sure it's clean, don't attach it.** Describe the files instead, as the
   [issue form](https://github.com/sanguity/sanguity/issues/new?template=portal-export.yml) asks, and a
   maintainer will say what they need.

## What it refuses

It treats the export as hostile, like Sanguity's own import does, and refuses rather than guesses:

- a zip with a path outside itself (`../`, an absolute path), a symbolic link, a password, names that clash, more
  than 20,000 files, more than 16 GB unpacked, a member that unpacks to far more than its size (a zip bomb), or zips
  nested more than three deep;
- an XML file that declares entities (it is left out of the copy, and named in the report);
- an export with nothing it can clean: only PDFs, spreadsheets (`.xlsx`: save it as CSV first), Word files or
  pictures; other archives (`.tar`, `.7z`, `.rar`: unpack them first).

It also won't write over anything: the output path must not exist, and can't be inside the export.

## For maintainers: from a sample to a fixture

A sample shared in an issue is still derived from someone's real records, and
[fixtures must be fictional](../CONTRIBUTING.md#rules-this-project-keeps). Before one goes into
`tests/fixtures/importers/<NAME>/`, check it again as above, cut it down to what the tests need, and change the
values too (`--jitter`, or by hand), so nothing in it is a real measurement of a real person. Then follow
[docs/importers.md](importers.md#fixtures-and-tests).

`tests/test_anonymise.py` runs the tool over every importer fixture and checks that the copy imports with the same
records, that none of the fixtures' names, identifiers or original dates are left, and that unsafe zips and files
are refused. When you add an importer, its fixture is covered without changes; if the copy of it doesn't import the
same, teach the tool the new format's fields.
