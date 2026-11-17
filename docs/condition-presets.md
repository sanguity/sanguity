# Dashboard presets by condition

**Settings → Dashboard → Start from a condition** (#186) fills in a profile's Overview with what's commonly followed
for one or more conditions, picked together: the key-lab tiles, the home readings' tiles, and, if ticked, a saved
Labs view per condition and the measures the visit summary ticks at first. The definitions are in
[`condition-presets.json`](../condition-presets.json), at the top of the repository beside `lab-aliases.json`;
[`server/presets.py`](../server/presets.py) reads them and [`www/js/presets.js`](../www/js/presets.js) draws the
picker.

## What a preset does, and doesn't

- **It only chooses what's shown.** Each condition's list is the labs and readings commonly followed for it according
  to a published guideline, which the picker names and links. Nothing judges a result or suggests a test: the labs a
  profile has no results for are named ("no results in this profile's records, so no tile"), never recommended.
- **Applied once, not linked.** Picking conditions shows a preview first. **Fill these in** changes the settings'
  editors on the page, unsaved: the person saves each section as any other change, **Discard changes** puts it back,
  and **Reset to defaults** undoes it after saving. Nothing about the preset is kept, so a later change to
  `condition-presets.json` never changes anyone's settings.
- **Add to my tiles** puts the preset's labs after the person's own; **Replace my tiles** uses only the preset's.
  Several conditions are interleaved by rank (each condition's first lab, then each one's second, ...), without
  repeats, and at most 12 tiles come from a preset. Only labs with results in the profile get a tile.
- **The problem list's suggestion:** a current problem (never an allergy, and not one marked resolved or inactive)
  that names a condition, by its name or a synonym as a whole word, is offered above the picker. Nothing is applied
  until the person chooses it and fills it in.
- **Children:** on a profile whose birth date (About this person) makes it under 18, a lab marked `"adult": true` is
  left out, and the preview says so.

## The file

```json
{
 "_about": "...", "_review": "...",
 "systems": [{"id": "kidney", "name": "Kidneys and urinary tract"}],
 "labs": {"9318-7": {"name": "Urine Albumin/Creatinine Ratio", "aka": ["UACR", "Albumin/Creatinine Ratio, Urine"]},
          "98979-8": {"name": "CKD-EPI eGFR", "adult": true}},
 "conditions": [
  {"id": "chronic-kidney-disease", "name": "Chronic kidney disease", "system": "kidney",
   "synonyms": ["CKD", "chronic renal failure"],
   "sources": [{"publisher": "KDIGO (Kidney Disease: Improving Global Outcomes)",
                "title": "KDIGO 2024 Clinical Practice Guideline for the Evaluation and Management of Chronic Kidney Disease",
                "year": 2024, "url": "https://doi.org/10.1016/j.kint.2023.10.018"}],
   "labs": ["98979-8", "2160-0", "9318-7", "2823-3"],
   "home": ["bp"],
   "reviewed": false}
 ]
}
```

- **`systems`**: the picker's groups, in order. A long list stays easy to scan grouped this way, with the search box
  above it (it matches names and synonyms).
- **`labs`** (the file's own table): a lab `lab-aliases.json` doesn't know, by LOINC code: the `name` its tile gets
  and other spellings (`aka`) to find it by in a profile whose series has no code. A code `lab-aliases.json` knows
  takes its name and spellings from there, and is listed here only to mark it `"adult": true`.
- **A condition**: `id` (lower-case words joined by hyphens, never changed once published), `name`, `system`,
  `synonyms` (for the search and the problem list; each name or synonym belongs to one condition only), `except`
  (optional: problems that contain a synonym but aren't this condition, like "pulmonary hypertension"), `sources`
  (one or more published guidelines: publisher, title, year and an `https://` address), `labs` (LOINC codes, most
  important first), `home` (some of `bp`, `glucose`, `temp`, `oxygen`, `weight`) and `reviewed`.
- **Matching a profile's labs:** a series matches a lab by its LOINC code, else by `lab-aliases.json`'s name for that
  code, else by any spelling of it (case aside). When several series match, the one with the most results is used.

## Adding a condition

1. Find a published guideline that lists what is commonly followed for the condition: a society's or a public
   body's, freely readable where possible. Cite it accurately (publisher, full title, year, a DOI or the
   publisher's own page).
2. Add the condition to `conditions`, its labs in the guideline's order of importance as best it says, by LOINC code.
   Look each code up at [loinc.org](https://loinc.org/search/); for a code `lab-aliases.json` doesn't know, add it to
   the file's `labs` with its common name and the spellings labs print. Mark a lab `"adult": true` when it's meant
   for adults only (an adult eGFR equation, say).
3. Leave `"reviewed": false`. A clinician's review of each definition before it ships is recommended; once one has
   checked it against the source, they (or you, saying who in the pull request) set it to `true`.
4. Run `python tests/test_condition_presets.py`. It checks every code's form and check digit, that each is named
   somewhere, that no lab is listed twice and no spelling stands for two tests, that each condition has a source,
   known home readings, and a name and synonyms no other condition uses.
5. Open a pull request with the guideline linked. Wording, here and in the app, stays "commonly followed for",
   never "you should test".
