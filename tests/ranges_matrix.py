"""The matrix of #752: one profile's fictional results that every page judges, draws and says the same way, for
tests/test_ranges.py (the server: the visit summary, its PDF, share links) and tests/browser_check.py (the page: Labs,
the Overview, Compare, a share link's page, the summary's preview and its PDF).

Each case is a lab whose results [days back, value, low, high, flag, as given] go through one of the ways a result's
range and flag were said differently: a report's range then none; none then a report's; a range from Settings then a
report's; a report's then one from Settings; a range that changes; and values given as "<" and ">" bounds at and past
each edge of a report's range and of one from Settings. JUDGED is what each result is judged as, by the one rule
(server/ranges.py judge, www/js/ranges.js applyRangeOverrides): [flag, low, high, source]; STEPS the band's steps, each
the results it covers, with its words (a result whose report gave no range is under none). The summary's "since" day is
SINCE days back, so each case has a Then and a Now. Fictional data only.
"""
import datetime

UNITS = {"Potassium": "mmol/L", "Sodium": "mmol/L", "Folate": "ng/mL", "Magnesium": "mg/dL", "Hemoglobin": "g/dL",
         "Amylase": "U/L", "Lipase": "U/L"}
OVERRIDES = [{"lab": "Folate", "unit": "ng/mL", "sex": "any", "low": 5, "high": 20},
             {"lab": "Magnesium", "unit": "mg/dL", "sex": "any", "low": 1.5, "high": 2.5},
             {"lab": "Lipase", "unit": "U/L", "sex": "any", "low": 15, "high": 60}]
SINCE = 45
R, S = "report", "settings"
CASES = {   # lab: [(days back, value, low, high, flag, as given)]
    # a report's range, then two results whose report gave none (#752's Potassium: its band ran on under 3.4 and 5.3)
    "Potassium": [(100, 3.2, 3.5, 5.1, "L", "3.2"), (40, 3.4, None, None, "", "3.4"), (10, 5.3, None, None, "", "5.3")],
    # none, then a report's range and no flag: the flag worked out against it
    "Sodium": [(100, 150, None, None, "", "150"), (10, 133, 135, 145, "", "133")],
    # a range from Settings, then the report's (#717)
    "Folate": [(100, 4, None, None, "", "4"), (40, 6, 3, 17, "", "6"), (10, 7, 3, 17, "", "7")],
    # the report's, then one from Settings (#752's Magnesium: 1.6 Low beside 1.5–2.5 from settings, its report's 1.7–2.4
    # said nowhere)
    "Magnesium": [(100, 1.6, 1.7, 2.4, "L", "1.6"), (10, 1.8, None, None, "", "1.8")],
    # a range that changes: 13 is low against the first, in the second
    "Hemoglobin": [(100, 13, 13.5, 17.5, "", "13.0"), (10, 13, 12, 16, "", "13.0")],
    # bounds at and past each edge of a report's 30–110, no flag on the report: "<30" and "<25" low, ">110" and ">120"
    # high, the rest neither (each may be inside it)
    "Amylase": [(100, 110, 30, 110, "", "<110"), (90, 120, 30, 110, "", "<120"), (80, 30, 30, 110, "", ">30"),
                (70, 25, 30, 110, "", ">25"), (40, 30, 30, 110, "", "<30"), (30, 25, 30, 110, "", "<25"),
                (20, 110, 30, 110, "", ">110"), (10, 120, 30, 110, "", ">120")],
    # the same against a range from Settings, 15–60 (#752's Lipase "<15": unflagged, on the band's edge)
    "Lipase": [(100, 60, None, None, "", "<60"), (80, 15, None, None, "", ">15"), (40, 15, None, None, "", "<15"),
               (30, 10, None, None, "", "<10"), (20, 60, None, None, "", ">60"), (10, 70, None, None, "", ">70")],
}
JUDGED = {   # lab: each result's [flag, low, high, source], as Labs, the summary and its PDF judge it
    "Potassium": [["L", 3.5, 5.1, R], ["", None, None, R], ["", None, None, R]],
    "Sodium": [["", None, None, R], ["L", 135, 145, R]],
    "Folate": [["L", 5, 20, S], ["", 3, 17, R], ["", 3, 17, R]],
    "Magnesium": [["L", 1.7, 2.4, R], ["", 1.5, 2.5, S]],
    "Hemoglobin": [["L", 13.5, 17.5, R], ["", 12, 16, R]],
    "Amylase": [["", 30, 110, R], ["", 30, 110, R], ["", 30, 110, R], ["", 30, 110, R],
                ["L", 30, 110, R], ["L", 30, 110, R], ["H", 30, 110, R], ["H", 30, 110, R]],
    "Lipase": [["", 15, 60, S], ["", 15, 60, S], ["L", 15, 60, S], ["L", 15, 60, S], ["H", 15, 60, S], ["H", 15, 60, S]],
}
STEPS = {   # lab: the band's steps, each [the results it covers (their indexes), its words]
    "Potassium": [[[0], "3.5–5.1 on report"]],
    "Sodium": [[[1], "135–145 on report"]],
    "Folate": [[[0], "5–20 from settings"], [[1, 2], "3–17 on report"]],
    "Magnesium": [[[0], "1.7–2.4 on report"], [[1], "1.5–2.5 from settings"]],
    "Hemoglobin": [[[0], "13.5–17.5 on report"], [[1], "12–16 on report"]],
    "Amylase": [[list(range(8)), "30–110 on report"]],
    "Lipase": [[list(range(6)), "15–60 from settings"]],
}
# the summary's trends since SINCE days back: each Then's and Now's cell (value, flag in words, the range it's judged
# against where that isn't the row's, else None) and the row's Now's range
TRENDS = {
    "Potassium": [("3.2", "Low", ["report range", "3.5–5.1"]), ("5.3", None, None), ""],
    "Sodium": [("150", None, ["no range given", ""]), ("133", "Low", None), "135–145"],
    "Folate": [("4", "Low", ["settings range", "5–20"]), ("7", None, None), "3–17"],
    "Magnesium": [("1.6", "Low", ["report range", "1.7–2.4"]), ("1.8", None, None), "1.5–2.5 (settings)"],
    "Hemoglobin": [("13.0", "Low", ["report range", "13.5–17.5"]), ("13.0", None, None), "12–16"],
    "Amylase": [(">25", None, None), (">120", "High", None), "30–110"],
    "Lipase": [(">15", None, None), (">70", "High", None), "15–60 (settings)"],
}
# the summary's out-of-range rows since then: [value, flag, range, out, of]
OUT_OF_RANGE = {"Sodium": ["133", "Low", "135–145", 1, 1], "Amylase": [">120", "High", "30–110", 4, 4],
                "Lipase": [">70", "High", "15–60 (settings)", 4, 4]}


def day(n, today=None):
    return ((today or datetime.date.today()) - datetime.timedelta(days=n)).isoformat() + "T08:00:00"


def records(today=None):
    """The profile's records, as an import stores them (records.store)."""
    labs = [{"name": n, "unit": UNITS[n], "panel": "", "n": len(pts), "txt": [],
             "pts": [[day(d, today), v, lo, hi, f, t] for d, v, lo, hi, f, t in pts]} for n, pts in CASES.items()]
    draws = sorted({d for pts in CASES.values() for d, *_ in pts}, reverse=True)   # a lab draw each day
    events = [{"id": i + 1, "t": day(d, today), "k": "lab", "title": "Lab results (fictional)"} for i, d in enumerate(draws)]
    events.append({"id": len(events) + 1, "t": day(SINCE, today), "k": "visit", "title": "Office visit (fictional)"})   # the summary's "since"
    return {"built": day(0, today)[:10], "source": "fictional", "events": events, "details": {}, "costs": [], "upcoming": [], "labs": labs}


def share_judged(lab):
    """What a share link says of each result (its report's range and flag only: it never sends a range from Settings)."""
    return [[f, lo, hi, R] if src == R else ["", None, None, R] for f, lo, hi, src in JUDGED[lab]]
