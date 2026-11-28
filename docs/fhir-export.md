# Export as FHIR

Sharing → Export everything has two formats. **Sanguity's own** zip holds everything, settings and the journal
included, for another Sanguity to load. **FHIR R4** (#205) is a standard bundle that other health apps, patient
portals and clinics can read, of the kinds of records ticked. This page says what goes in it and what each record
becomes. The code is [`server/fhir_export.py`](../server/fhir_export.py); the route is
`GET /api/profiles/{pid}/export?format=fhir&include=<parts>&cycle=1` (owners and editors, as Export everything).

Nothing is sent anywhere: the zip is made on the server and downloaded by the person who asked for it, and the
activity log says it was exported, in which format and with which parts.

## The zip

| File | |
|---|---|
| `bundle.json` | A FHIR R4 (4.0.1) `Bundle` of type `collection`: the `Patient` first, then every record as a resource, then the `Practitioner`s, `Organization`s, `Location`s, `Device`s and `Coverage`s the records name |
| `documents/<id>/<name>` | Each document's file, as it was uploaded, with an HTML record's pictures and stylesheets beside it. Its `DocumentReference` points at it by a path relative to `bundle.json` (percent-encoded, as a `url` must be) |
| `letters/<n>.html` | A letter written in the records themselves (a portal's letters), as the file its `DocumentReference` points at |
| `README.txt` | What's in it, by resource type, and what was left out |

**Files beside the bundle, never inline.** A FHIR attachment can carry its file as base64 inside the JSON, but files
can be large (a scanned report, a DICOM image), base64 makes them a third larger, and every reader would have to hold
them in memory to read the bundle at all. Beside it, in the zip, they stream, and each one opens on its own. Sanguity's
own FHIR importer reads files beside the bundle and skips inline ones (with a warning), so the round trip works too.

**Written as it's sent.** The bundle is written a resource at a time straight into the zip as it downloads (like
Export everything, [docs/performance.md](performance.md)): a profile with ten years of phone data never sits in
memory or on disk as a whole.

## The records as the app shows them

The bundle holds the records with every correction made in Sanguity applied (#916), as the page shows them: a hidden
record is left out, two merged records are one resource (the first's, with what the other added), a renamed or
re-dated one has its new title or time, a change of category and "Not a hospital stay" are made, and a result two
sources gave goes once, linked as the same result or not (#947: copies the page shows once, as one Observation in each
report that holds it). A bundle has nowhere to carry the corrections apart, and
another app has nothing to apply them with. Sanguity's own zip is the other way round: its `data.json` holds the
records as they were imported, with the corrections beside them (#589), so another Sanguity loads them and they can
still be undone there.

## Ids

Every resource's id is a version 5 UUID made from the profile and what names the record: its source key when the
importer gave one (`fhir:Observation/abc`, `loinc:718-7`...), else its kind, time and title. Exported again, the same
record has the same id, so another app can tell an update from a new record. `fullUrl` is `urn:uuid:<id>`, and every
reference in the bundle is to a `fullUrl` in it. Two records with nothing at all to tell them apart get ids in the
order they come.

## What becomes what

US Core's conventions are followed where they apply: LOINC codes for vital signs and, where Sanguity knows them
([lab-aliases.json](../lab-aliases.json)), for lab results; UCUM units (`mm[Hg]`, `Cel`, `[lb_av]`, `10*3/uL`...)
beside the unit as reported; CVX vaccine codes; RxNorm and NDC drug codes; HL7's category and status code systems.
Vital signs whose unit is a UCUM unit claim FHIR's own vital-signs profiles (`meta.profile`: `bp`, `heartrate`,
`bodyweight`, `bodyheight`, `bodytemp`, `oxygensat`, `resprate`). Resources don't claim US Core profiles: US Core's
Patient needs an identifier from a health system and an administrative gender, which Sanguity doesn't hold.

| Sanguity | FHIR |
|---|---|
| A lab report (a day's panel) | `DiagnosticReport` (category `LAB`), its `result`s `Observation`s (category `laboratory`): the value (a `Quantity` of the number it was imported as, with its comparator where it's given as a bound, `<0.5`, `=<5`, `≥ 60`; else a string, #870), the unit, **each result's own reference range** (#58: the numbers and the text as printed; a limit that leaves its edge out, `<5`, in the text alone, said from its number, `<1250`, where the text as printed reads otherwise where the decimal mark isn't known, `<1,250` from a spreadsheet whose commas group thousands; a range its import couldn't read in a note, not as one) and its flag as an `interpretation`, a row's as its report printed it read by the one rule (`common.flag_code`, #835: `H`, `L`, `HH`, `LL`, `A` or `AA`; a flag that says no way, "Abnormal", is `A`). Each result once, at its own time: a report's row is its result's, found at the report's time or else on its day in the same import, the nearest (#876: a C-CDA organizer's results at times of their own went out twice) |
| A result in a lab series but in no report (typed in, or a source without reports) | an `Observation` on its own |
| Vitals at visits | `Observation`s (category `vital-signs`); **blood pressure as one Observation** (`85354-9`) with systolic and diastolic components; clinic weights as body weight (`29463-7`, in lb) |
| Visit | `Encounter` (class `AMB`): its type, who, where, the reasons; the visit's note and procedures in its narrative |
| Hospital stay | `Encounter` (class `IMP`) with its admission and discharge |
| Upcoming visit | `Appointment` (`booked`) |
| Condition | `Condition` (problem list, or health concern), its clinical status coded and as written |
| Allergy | `AllergyIntolerance`: substance and its code, reactions, severity, criticality, status |
| Family history | `FamilyMemberHistory` per relative's entry (#1134): relationship (its v3 RoleCode where the words are one), name, each condition with its code, onset age and contributed to death, deceased and the age at death, the note |
| Medication | `MedicationStatement`, based on a contained `MedicationRequest` that names the prescriber when the record does; RxNorm code, instructions, start and end |
| Pharmacy fill | `MedicationDispense`: pharmacy, days' supply, quantity, prescriber |
| Immunization | `Immunization`: CVX code, dose number, amount, route, site, lot, manufacturer, who gave it and where |
| Procedure | `Procedure`, with its reason, body site, outcome and notes |
| Imaging report, other study | `DiagnosticReport` (category `RAD`, or `OTH`) with its conclusion; one read from an `ImagingStudy` is an `ImagingStudy` again (modality, series, body site) |
| Document, letter | `DocumentReference`, the file beside the bundle |
| Messages | a `Communication` per message, each answering the one before (`inResponseTo`), the topic the conversation's subject |
| Claim | `ExplanationOfBenefit` in CARIN Blue Button's shape (claim type, the claim number, lines with their codes, billed, plan paid and your share as `total`s, network status, days' supply and Rx number for a fill), with a `Coverage` per insurer and plan |
| Home readings: blood pressure, temperature, blood oxygen, glucose | `Observation`s with **the Patient as performer**, and the device that took the reading as `Observation.device` (a `Device` named as the phone named it, with the app it came through as its property `app`). FHIR R4's performer can't be a Device, so the Patient (they took it at home) is the performer and the device is where R4 puts one. A reading typed in has no device. Blood pressure is one Observation with systolic and diastolic components and the pulse as a third (heart rate, `8867-4`); glucose in mg/dL, with the meal it was taken around when given |
| The phone's daily measures | an `Observation` a day each (steps, sleep, resting heart rate, weight from the scale...), with a LOINC code where one says the same thing; **CGM days as their daily summaries** (average, low, high, time in each range), never each five-minute reading; a meter's readings one by one |
| ECG | an `Observation` (MDC `131328`) with **the device's classification as it said it** ("Sinus Rhythm", "Inconclusive"), its average heart rate, and **the waveform as `SampledData`** in µV (MDC `131329` for lead I): a 30-second recording at 512 Hz is 15,360 samples, about 70 KB, so it's included, up to `ecg.MAX_SAMPLES` |
| Irregular rhythm notification | an `Observation`, its start and end |
| About this person: birth date | `Patient.birthDate` |
| About this person: the sex used for reference ranges | the `patient-sexParameterForClinicalUse` extension on the Patient (`female-typical` or `male-typical`), **never `Patient.gender`** |
| About this person: heights | body height `Observation`s (`8302-2`, in cm) |
| Cycle records (#183) | only when ticked for this export, as Export everything: `Observation`s (a period, each day's flow `49033-4`, ovulation tests, cervical mucus, spotting) |

**The sex used for reference ranges.** Sanguity keeps it apart from gender, as HL7's Gender Harmony model does:
`Patient.gender` is administrative gender, which Sanguity doesn't ask for and so never writes. The
[sex parameter for clinical use](https://build.fhir.org/ig/HL7/fhir-extensions/StructureDefinition-patient-sexParameterForClinicalUse.html)
extension is Gender Harmony's element for exactly this, "the sex to use for reference ranges", available to R4 in
HL7's extensions pack. An Observation of sex would claim a finding someone measured on a day; Sanguity's setting is a
standing choice with no date or measurer, so the extension is the truer fit.

### Sanguity's own codes

Where no standard code says what a record is, the export uses code systems named after this page; other apps show their
`display` and `text`, and Sanguity's FHIR importer reads them back exactly.

#### measures

`https://github.com/sanguity/sanguity/blob/main/docs/fhir-export.md#measures`: the phone's daily measures, by the
key the watch receiver gives them (`steps`, `hr_rest`, `hr_avg`, `hr_min`, `hr_max`, `hrv`, `spo2_avg`, `spo2_min`,
`resp`, `vo2max`, `sleep_h`, `sleep_deep_min`, `sleep_rem_min`, `sleep_light_min`, `sleep_awake_min`, `weight`,
`body_fat`, `lean_mass`, `bmr`, `distance_km`, `active_cal`, `bp_sys`, `bp_dia`, `bp_sys_max`, `bp_dia_max`, `bp_n`,
`body_temp`, `body_temp_max`, `glucose_meter`, `glucose_avg`, `glucose_min`, `glucose_max`, `glucose_n`,
`glucose_hours`, `glucose_lt54`, `glucose_lt70`, `glucose_in`, `glucose_gt180`, and any other the phone sends), each
with its name as `display`; `ecg` and `irregular_rhythm`; `cycle_period`, `cycle_flow` and `cycle_<kind>` for cycle
records; `app` (a Device's property: the app a reading came through) and `meal` (a glucose reading's component).

#### measure-groups

`https://github.com/sanguity/sanguity/blob/main/docs/fhir-export.md#measure-groups`: a daily measure's group on Body
and activity (`Activity`, `Heart`, `Sleep`, `Body`, `Glucose`...), as a second `category`.

#### record-kinds

`https://github.com/sanguity/sanguity/blob/main/docs/fhir-export.md#record-kinds`: the kind of record a
`DocumentReference` or `Communication` was shown as on the timeline, when it isn't a plain document or message
(`letter`, `imaging`, `appt_msg`...), as a `category`.

## What isn't in it

A FHIR bundle has no place for these, so they stay out, and the page and README.txt say so: **the journal**, the
profile's **settings** (other than About this person), **workouts**, **out-of-pocket payments**, the **activity log**
and the **original uploads**. Whatever is unticked stays out too, and cycle records unless ticked for this export.
README.txt also counts anything else left out, such as a document whose file is no longer on the server. For
everything, settings and the journal included, use Sanguity's own format.

## Reading it back

Sanguity's own FHIR importer reads the zip (upload it on Sources → Upload files) into **the same records**:
`tests/test_fhir_export.py` exports the demo profile (as the browser check builds it: labs with their own ranges,
vitals, visits, a stay, conditions, allergies, medications, fills, immunizations, procedures, studies, documents,
letters, messages, claims, home readings from the phone and typed in, a meter's and a CGM's glucose, ECGs with their
waveforms) through the route, uploads it into an empty profile through the import page's API, and compares the two
profiles record by record; then the same for every FHIR importer fixture (imported, exported, imported again). For
that, the importer reads what Sanguity writes: home readings (an Observation the Patient measured) into the watch
summary, the daily measures and ECGs by the codes above, conversations from `Communication`s, a statement's prescriber
from its request, a visit's note from its narrative, and a result's value with its precision as written ("2.0", not
"2").

What comes back differently, as FHIR or the importer have it:

- Times without an offset get the exporting person's time zone's offset for that day (FHIR requires one), so they show
  at the same wall-clock time; dates stay dates, except an upcoming visit's, which is noon (an Appointment needs a time).
- A condition's or allergy's source (the clinic that listed it) has no element in R4's Condition and AllergyIntolerance.
- Status words are FHIR's: a medication listed as "Discontinued" comes back "Stopped"; a claim's status and care
  category are worked out again from its amounts and lines, and a pharmacy claim is a fill (`rx`), as FHIR claims are.
- A letter comes back as the HTML file it was written to, shown under the letter's kind.
- A CGM day's readings counted by whole mg/dL aren't exported (only each day's summary is), so after loading the export
  back, the time in a person's own glucose range (#234) isn't known for those days; the standard ranges' times are.
  Nor are each day's hours (#303: each hour's count and percentiles), so those days aren't in the 24-hour profile.
- About this person's heights come back as a body height series, offered on About this person to add, as a clinic's
  are: an import never changes a setting. The birth date and the sex for reference ranges are on the Patient for other
  apps; an import into Sanguity doesn't set them either.
- Cycle records are left out of the import (with a warning), as loading Sanguity's own export leaves them out:
  cycle tracking is turned on for each profile, and its records come only from the phone apps.

## Validation

`tests/test_fhir_export.py` validates every bundle it makes against HL7's official FHIR R4 JSON schema
(`fhir.schema.json`, R4 4.0.1). The official file is 3.4 MB, so the repository keeps the part of it the export's
resource types reach, unchanged but for the descriptions, in
[`tests/fixtures/fhir-r4/fhir.schema.subset.json`](../tests/fixtures/fhir-r4/fhir.schema.subset.json) (130 KB; FHIR is
CC0, public domain), made by [`tools/fhir_schema_subset.py`](../tools/fhir_schema_subset.py), which records the
official file's SHA-256. [`tests/fhir_schema_check.py`](../tests/fhir_schema_check.py) checks against it with the few
JSON Schema keywords it uses, standard library only. The JSON schema checks structure, types, codes and formats (a
time without an offset fails); it doesn't check FHIR's invariants or profiles, which need HL7's Java validator.
