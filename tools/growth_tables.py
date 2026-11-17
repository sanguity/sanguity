#!/usr/bin/env python3
"""Make www/js/growth-lms.json, the growth charts' LMS tables the records page reads for a child's profile (#196), from
the published files, and check it against them. docs/growth-charts.md says where each comes from, its version and
licence; this script holds their addresses and SHA-256, and refuses a file that differs.

  WHO Child Growth Standards, birth to 2 years: weight, length and head circumference for age, from the WHO's own
    `anthro` package (GPL-3, the WHO its copyright holder), data-raw/growthstandards/*.txt at a pinned commit: an LMS
    row for every day of age. Kept: every day to 13 weeks (day 91), when growth is fastest, then every 7th day to
    day 728, and day 730 (2 years); an age between two kept days is read between their rows, which stays within 0.1
    of a percentile point of the daily tables (checked below).
  CDC growth charts, 2 to 20 years: weight and stature for age (2000, unchanged since), and BMI for age from the 2022
    extended charts (the 2000 LMS below the 95th percentile, and the scale used above it), public domain. Kept whole.

Usage, with the files downloaded into one folder (the names below; the script makes no request itself):

  python3 tools/growth_tables.py <folder> [--out www/js/growth-lms.json]

It prints the largest difference between a percentile the bundled tables give and the published one: the CDC's
percentile columns, and the WHO's daily tables for every day to 2 years.
"""
import argparse, csv, hashlib, json, math, os, sys

ANTHRO = "https://raw.githubusercontent.com/WorldHealthOrganization/anthro/b776d8a12b1c97369c748b561159fd2ec4f4db58/data-raw/growthstandards/"
CDC = "https://www.cdc.gov/growthcharts/data/"
SOURCES = {   # file name: (address, SHA-256)
    "weianthro.txt": (ANTHRO + "weianthro.txt", "bc15a6a623dd1d5beaeed1497666332aa54bc4ccd15ff9658c487d79694ab77b"),
    "lenanthro.txt": (ANTHRO + "lenanthro.txt", "709f7a11881451daf7820f022d363d5bdb93746b5361d6bd9218af6ff838e0c2"),
    "hcanthro.txt": (ANTHRO + "hcanthro.txt", "e794e46f06b91223ad2c6435148dc08794a1d75b67613a652c3151201a98bf7c"),
    "wtage.csv": (CDC + "zscore/wtage.csv", "3406c9d125bcb69c062a9e84eb8c0209bfe9346542bdc1d308643750dcc241b7"),
    "statage.csv": (CDC + "zscore/statage.csv", "45130d2a9d7c50c54a47e7ba626b66c61d4554bc2d901198cedd9419a53f7251"),
    "bmi-age-2022.csv": (CDC + "extended-bmi/bmi-age-2022.csv", "7f416a213157a5209bb6cbfd1b19292510248f3f31fe43c9ff63f6d8e39f7890"),
}
MONTH = 30.4375                    # days: the WHO's and the CDC's month
WHO_DAYS = list(range(92)) + list(range(98, 729, 7)) + [730]   # every day to 13 weeks, then every week, and 2 years
WHO_CURVES, CDC_CURVES = [3, 15, 50, 85, 97], [5, 10, 25, 50, 75, 90, 95]


# ---------------------------------------------------------------- LMS (www/js/growth.js does the same on the page)
def phi(z):
    return 0.5 * math.erfc(-z / math.sqrt(2))


def z_of(x, L, M, S):
    return math.log(x / M) / S if abs(L) < 1e-12 else ((x / M) ** L - 1) / (L * S)


def x_of(z, L, M, S):
    return M * math.exp(S * z) if abs(L) < 1e-12 else M * (1 + L * S * z) ** (1 / L)


def z_who(x, L, M, S, restricted):
    """The WHO's z-score: beyond ±3 SD, for weight and head circumference (not length), measured in the distance between
    the 2 and 3 SD lines, as the WHO's anthro software does."""
    z = z_of(x, L, M, S)
    if restricted and abs(z) > 3:
        s = 1 if z > 0 else -1
        sd3, sd2 = x_of(3 * s, L, M, S), x_of(2 * s, L, M, S)
        z = s * 3 + (x - sd3) / abs(sd3 - sd2)
    return z


def pct_cdc_bmi(x, L, M, S, sigma):
    """The CDC's 2022 extended BMI-for-age: the LMS below the 95th percentile; above it, 90 + 10 × Φ((BMI − P95) / σ)."""
    p95 = x_of(1.6448536269514722, L, M, S)
    return 100 * phi(z_of(x, L, M, S)) if x < p95 else 90 + 10 * phi((x - p95) / sigma)


def lms_at(rows, age):
    """[L, M, S...] at an age, linearly between the two rows around it (rows: [[age, L, M, S...]] by age)."""
    lo, hi = 0, len(rows) - 1
    if not rows[0][0] <= age <= rows[-1][0]:
        return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if rows[mid][0] <= age:
            lo = mid
        else:
            hi = mid
    a, b = rows[lo], rows[hi]
    f = 0 if b[0] == a[0] else (age - a[0]) / (b[0] - a[0])
    return [x + (y - x) * f for x, y in zip(a[1:], b[1:])]


def percentile(tables, key, sex, days, x):
    """A measurement's percentile from the bundled tables (growth-lms.json, loaded), as www/js/growth.js's growthPct
    works it out: key "weight" (kg), "height" (cm), "head" (cm) or "bmi"; sex "female" or "male"; days of age (corrected
    already). None when the charts have none for it."""
    s = {"male": "1", "female": "2"}.get(sex)
    if not s or days < 0 or days > 240 * MONTH or not x > 0:
        return None
    if days <= 730:
        k = {"weight": "weight", "height": "length", "head": "head"}.get(key)
        p = k and lms_at(tables["who"][k][s], days)
        return None if not p else 100 * phi(z_who(x, *p, restricted=k != "length"))
    k = {"weight": "weight", "height": "stature", "bmi": "bmi"}.get(key)
    p = k and lms_at(tables["cdc"][k][s], days / MONTH)
    if not p:
        return None
    return pct_cdc_bmi(x, *p) if k == "bmi" else 100 * phi(z_of(x, *p[:3]))


# ---------------------------------------------------------------- reading the published files
def checked(folder, name):
    path = os.path.join(folder, name)
    with open(path, "rb") as f:
        data = f.read()
    want = SOURCES[name][1]
    if hashlib.sha256(data).hexdigest() != want:
        sys.exit(f"{name} isn't the file this script knows (SHA-256 {want}); download it again from {SOURCES[name][0]}")
    return data.decode("utf-8-sig")


def who_daily(folder, name):
    rows = list(csv.DictReader(checked(folder, name).splitlines(), delimiter="\t"))
    out = {"1": [], "2": []}
    for r in rows:
        if int(r["age"]) <= 731:
            out[r["sex"]].append([int(r["age"]), float(r["l"]), float(r["m"]), float(r["s"])])
    return out


def cdc_rows(folder, name, sigma=False):
    out = {"1": [], "2": []}
    published = {"1": [], "2": []}
    for r in csv.DictReader(checked(folder, name).splitlines()):
        r = {k.lower(): v for k, v in r.items()}
        age = float(r["agemos"])
        if age > 240:
            continue
        row = [age, float(r["l"]), float(r["m"]), float(r["s"])] + ([float(r["sigma"])] if sigma else [])
        out[r["sex"]].append(row)
        published[r["sex"]].append((age, {k: float(v) for k, v in r.items() if k.startswith("p") and k[1:].replace("_", "").isdigit()}))
    return out, published


def rnd(v, sig=7):
    return float(f"{v:.{sig}g}")


def build(folder):
    who = {"ageUnit": "days", "curves": WHO_CURVES}
    who_check = []
    for key, name, restricted in (("weight", "weianthro.txt", True), ("length", "lenanthro.txt", False), ("head", "hcanthro.txt", True)):
        daily = who_daily(folder, name)
        who[key] = {}
        for sex, rows in daily.items():
            kept = []
            for d in WHO_DAYS:
                L, M, S = lms_at(rows, d)
                kept.append([round(d, 4), round(L, 4), round(M, 4), round(S, 5)])
            who[key][sex] = kept
            # every day's measurement at the 1st, 3rd, 50th, 97th and 99th percentiles, by the kept rows
            for day_, L, M, S in rows:
                if day_ > 730:
                    continue
                k = lms_at(kept, day_)
                for z in (-2.326, -1.881, 0, 1.881, 2.326):
                    x = x_of(z, L, M, S)
                    who_check.append(abs(100 * phi(z_who(x, *k, restricted)) - 100 * phi(z)))
    cdc = {"ageUnit": "months", "curves": CDC_CURVES, "bmiCurves": [5, 10, 25, 50, 75, 85, 90, 95]}
    cdc_check = []
    for key, name, sigma in (("weight", "wtage.csv", False), ("stature", "statage.csv", False), ("bmi", "bmi-age-2022.csv", True)):
        rows, published = cdc_rows(folder, name, sigma)
        cdc[key] = {sex: [[r[0]] + [rnd(v) for v in r[1:]] for r in rs] for sex, rs in rows.items()}
        for sex, pub in published.items():
            for age, ps in pub:
                k = lms_at(cdc[key][sex], age)
                for col, x in ps.items():
                    want = float(col[1:].replace("_", "."))
                    if col in ("p99_99", "p99_9"):
                        continue   # the extended chart's far tail, beyond what Sanguity says (above the 99th)
                    got = pct_cdc_bmi(x, *k) if sigma else 100 * phi(z_of(x, *k[:3]))
                    cdc_check.append(abs(got - want))
    out = {"about": "Growth charts' LMS tables for Sanguity's growth percentiles (#196), made by tools/growth_tables.py. "
                    "Sources, versions and licences: docs/growth-charts.md. The WHO part is from the WHO's anthro package "
                    "(GPL-3); the CDC part is public domain.",
           "sources": {n: {"url": u, "sha256": h, "licence": "GPL-3, © World Health Organization (the anthro package)" if "anthro" in n
                           else "public domain (US government work, CDC/NCHS)"} for n, (u, h) in SOURCES.items()},
           "who": who, "cdc": cdc}
    return out, max(who_check), max(cdc_check)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "www", "js", "growth-lms.json"))
    a = ap.parse_args()
    out, who_err, cdc_err = build(a.folder)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
        f.write("\n")
    print(f"wrote {a.out} ({os.path.getsize(a.out):,} bytes); largest difference from the published percentiles: "
          f"WHO {who_err:.4f}, CDC {cdc_err:.4f} percentile points")


if __name__ == "__main__":
    main()
