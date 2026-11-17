# Growth charts: where the tables come from, and their licences

For a child's profile (a birth date and a sex used for reference ranges in About this person, and measurements taken
before 20), Body and activity shows where each height, weight, BMI and head circumference falls on the published growth
charts for age and sex (#196). It shows where the measurements fall and which chart they were read on; it doesn't say
what that means, and it isn't medical advice.

The charts are the convention the CDC recommends for the United States: the **WHO Child Growth Standards from birth to
2 years**, and the **CDC growth charts from 2 to 20 years**. The tables they are drawn from are bundled with Sanguity in
[`www/js/growth-lms.json`](../www/js/growth-lms.json) (about 80 KB, 33 KB as sent), which the records page fetches only
for such a profile. The app makes no request anywhere else for them.

[`tools/growth_tables.py`](../tools/growth_tables.py) makes that file from the files listed below, refusing any whose
SHA-256 differs, and checks it against them: a percentile from the bundled tables is within 0.07 of a percentile point
of the WHO's daily tables for every day to 2 years, and within 0.002 of the CDC's published percentile columns.
`tests/test_growth.py` checks the bundled file against published values too.

## The tables

| Part | Published as | Version | Source file and SHA-256 | Licence |
|---|---|---|---|---|
| WHO: weight for age, birth to 2 years | WHO Child Growth Standards (2006), LMS by day of age | the WHO's `anthro` R package, commit `b776d8a` (30 January 2026; CRAN version 1.1.0) | [`data-raw/growthstandards/weianthro.txt`](https://github.com/WorldHealthOrganization/anthro/blob/b776d8a12b1c97369c748b561159fd2ec4f4db58/data-raw/growthstandards/weianthro.txt) `bc15a6a623dd1d5beaeed1497666332aa54bc4ccd15ff9658c487d79694ab77b` | GPL-3 |
| WHO: length for age, birth to 2 years | as above | as above | [`lenanthro.txt`](https://github.com/WorldHealthOrganization/anthro/blob/b776d8a12b1c97369c748b561159fd2ec4f4db58/data-raw/growthstandards/lenanthro.txt) `709f7a11881451daf7820f022d363d5bdb93746b5361d6bd9218af6ff838e0c2` | GPL-3 |
| WHO: head circumference for age, birth to 2 years | as above | as above | [`hcanthro.txt`](https://github.com/WorldHealthOrganization/anthro/blob/b776d8a12b1c97369c748b561159fd2ec4f4db58/data-raw/growthstandards/hcanthro.txt) `e794e46f06b91223ad2c6435148dc08794a1d75b67613a652c3151201a98bf7c` | GPL-3 |
| CDC: weight for age, 2 to 20 years | CDC growth charts (2000), LMS by half month | unchanged since 30 May 2000 | [`wtage.csv`](https://www.cdc.gov/growthcharts/data/zscore/wtage.csv) `3406c9d125bcb69c062a9e84eb8c0209bfe9346542bdc1d308643750dcc241b7` | public domain |
| CDC: stature for age, 2 to 20 years | as above | as above | [`statage.csv`](https://www.cdc.gov/growthcharts/data/zscore/statage.csv) `45130d2a9d7c50c54a47e7ba626b66c61d4554bc2d901198cedd9419a53f7251` | public domain |
| CDC: BMI for age, 2 to 20 years | CDC extended BMI-for-age growth charts (December 2022): the 2000 LMS, with the scale used above the 95th percentile | 2022 | [`bmi-age-2022.csv`](https://www.cdc.gov/growthcharts/data/extended-bmi/bmi-age-2022.csv) `7f416a213157a5209bb6cbfd1b19292510248f3f31fe43c9ff63f6d8e39f7890` | public domain |

Kept from the WHO's daily tables: every day to 13 weeks, when a baby grows fastest, then every 7th day to day 728,
and day 730 (2 years). An age between two kept days is read between their rows. The CDC's tables are kept whole (their
ages are half months: 24.5 stands for 24.0 to 24.99 months), and read between rows the same way.

## The licences, checked

**CDC.** The CDC growth charts and their data files are works of the US federal government (the National Center for
Health Statistics), which aren't subject to copyright in the United States (17 U.S.C. § 105). The CDC says so of its
site: "Most of the information on the CDC and ATSDR websites is not subject to copyright, is in the public domain, and
may be freely used or reproduced without obtaining copyright permission"
([cdc.gov/other/agencymaterials.html](https://www.cdc.gov/other/agencymaterials.html)). Citing the source is asked
for, and done here and on the chart.

**WHO.** The WHO's own publications, including the tables and charts on
[who.int/tools/child-growth-standards](https://www.who.int/tools/child-growth-standards), are © WHO, mostly under
Creative Commons Attribution-NonCommercial-ShareAlike 3.0 IGO, and other uses (such as "reproduction and translation
of figures, tables") need the WHO's permission ([who.int/about/policies/publishing/copyright](https://www.who.int/about/policies/publishing/copyright)).
A non-commercial licence can't be combined with Sanguity's AGPL-3.0, so **those files are not what Sanguity bundles.**

The same standards are also published by the WHO as part of its R package
[`anthro`](https://github.com/WorldHealthOrganization/anthro) ("Computation of the WHO Child Growth Standards"), whose
`DESCRIPTION` names the World Health Organization as copyright holder (`cph`) and states `License: GPL-3`, with the
GPL-3 text as the repository's `LICENSE.md`. Its `data-raw/growthstandards/` folder holds the LMS tables by day of
age, the same numbers as the WHO's expanded tables on who.int (checked: the WHO's published percentiles for a day are
what these give). GPL-3 section 13 (and AGPL-3.0 section 13) allows a GPL-3 work to be combined with an AGPL-3.0 one
and the combination conveyed, each part keeping its own licence. So the WHO part of `growth-lms.json` is a subset of
those GPL-3 files, under GPL-3; the CDC part is public domain; and the rest of Sanguity stays AGPL-3.0-or-later.

If you redistribute Sanguity, `growth-lms.json` goes with this note: its WHO part is from the WHO's `anthro` package,
© World Health Organization, licensed under the GNU General Public License version 3.

## How a percentile is worked out

- **Age.** Days from the birth date to the measurement's day. Before 2 years, for a baby born early (a due date in About
  this person, after the birth date), the age is corrected: the days between the birth date and the due date are taken
  off, the usual practice until 2 years. A measurement before the due date has no percentile.
- **Which chart.** Under 2 years (730 days), the WHO's: weight, length (lying down) and head circumference. From 2 to
  20 years, the CDC's: weight, stature (standing) and BMI. Head circumference after 2 and BMI before 2 aren't shown.
- **The LMS method.** For a measurement X and the table's L, M and S at that age, z = ((X/M)^L − 1) / (L·S) (or
  ln(X/M)/S when L is 0), and the percentile is the normal distribution's share below z. The WHO's weight and head
  circumference beyond ±3 SD are measured in the distance between the 2 and 3 SD lines, as the WHO's software does.
  The CDC's BMI above the 95th percentile follows the 2022 extended charts: 90 + 10 × Φ((BMI − P95) / σ).
- **Shown.** As a whole percentile ("62nd"), or "below the 1st" and "above the 99th"; the curves drawn are the ones
  each publisher's charts show: the WHO's 3rd, 15th, 50th, 85th and 97th, the CDC's 5th, 10th, 25th, 50th, 75th, 90th
  and 95th (and the 85th for BMI).
- **BMI.** Each weight with the height in effect on its day (About this person's history; a child's height from the
  3 months before the weight), as on the BMI chart (#195).
