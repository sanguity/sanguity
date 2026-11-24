"""The matrix of #752: one profile's fictional results that every page judges, draws and says the same way, for
tests/test_ranges.py (the server: the visit summary, its PDF, share links) and tests/browser_check.py (the page: Labs,
the Overview, Compare, a share link's page, the summary's preview and its PDF; #782: the Timeline's counts, the drawer's
rows, the heatmap and a converted value's "Reported … ref").

Each case is a lab whose results [days back, value, low, high, flag, as given] go through one of the ways a result's
range and flag were said differently: a report's range then none; none then a report's; a range from Settings then a
report's; a report's then one from Settings; a range that changes; values given as "<" and ">" bounds at and past
each edge of a report's range and of one from Settings; and one-sided ranges that leave their edge out ("<5", ">60") or
keep it ("<=5", ">=60"), at and past it, through the importers that read them too (#780). JUDGED is what each result is
judged as, by the one rule (server/ranges.py judge, www/js/ranges.js applyRangeOverrides): [flag, low, high, source],
and EDGES its edge where its range has one; STEPS the band's steps, each the results it covers, with its words (a result
whose report gave no range is under none). The summary's "since" day is SINCE days back, so each case has a Then and a
Now. Each draw day is a lab day with one result listing that day's results as a report prints them (#782): its row named
as the report names the test (ROW_NAMES: "CHLORIDE" for the series "Chloride, Blood", "CREAT" for "Creatinine"), the
value as given, the range as printed (a one-sided one with its edge, "<5" or "<=5") and the report's own flag, and the
count of flags its importer stored. COPIES (#781, at the end) is a second profile's: one result twice (from two
imports) and a lab in two units, before and after "Same result". Fictional data only.
"""
import datetime

UNITS = {"Potassium": "mmol/L", "Sodium": "mmol/L", "Folate": "ng/mL", "Magnesium": "mg/dL", "Hemoglobin": "g/dL",
         "Amylase": "U/L", "Lipase": "U/L", "Chloride, Blood": "mmol/L", "Creatinine": "mg/dL"}
OVERRIDES = [{"lab": "Folate", "unit": "ng/mL", "sex": "any", "low": 5, "high": 20},
             {"lab": "Magnesium", "unit": "mg/dL", "sex": "any", "low": 1.5, "high": 2.5},
             {"lab": "Lipase", "unit": "U/L", "sex": "any", "low": 15, "high": 60},
             {"lab": "Creatinine", "unit": "mg/dL", "sex": "any", "low": 0.6, "high": 1.2}]
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
    # #782: twenty results (a row on Labs' heatmap) in a report's 98–107, two flagged by their report with no range
    # printed: the heatmap's cell, mark and count by the flag (it was a grey "no range" cell, counted 0); its rows
    # printed "CHLORIDE". The latest its report flags "A" inside its range, as the PDF importer keeps it: a report's own
    # flag first, ◆ in every view (it was kept with none, and said only by the drawer's row)
    "Chloride, Blood": [(200 - 10 * i, v, None if i in (14, 17) else 98, None if i in (14, 17) else 107,
                         {14: "H", 17: "L", 19: "A"}.get(i, ""), str(v)) for i in range(20) for v in [{14: 112, 17: 92}.get(i, 100 + i % 5)]],
    # #782: a range from Settings, a report's with no flag and a report's one-sided, its rows printed "CREAT": the drawer
    # flags each as Labs does (a Settings flag naming its range), the Timeline's badge counts them; in SI units
    # "Reported … ref" says the report's range only, as Labs says it ("≤ 1.3", not "–1.3"; none from Settings)
    "Creatinine": [(100, 1.5, None, None, "", "1.5"), (70, 0.5, None, None, "", "0.5"), (40, 1.4, 0.7, 1.3, "", "1.4"),
                   (20, 1.0, None, 1.3, "", "1.0"), (10, 1.4, None, 1.3, "", "1.4")],
}
ROW_NAMES = {"Chloride, Blood": "CHLORIDE", "Creatinine": "CREAT"}   # a row as its report names the test (#782)
JUDGED = {   # lab: each result's [flag, low, high, source], as Labs, the summary and its PDF judge it
    "Potassium": [["L", 3.5, 5.1, R], ["", None, None, R], ["", None, None, R]],
    "Sodium": [["", None, None, R], ["L", 135, 145, R]],
    "Folate": [["L", 5, 20, S], ["", 3, 17, R], ["", 3, 17, R]],
    "Magnesium": [["L", 1.7, 2.4, R], ["", 1.5, 2.5, S]],
    "Hemoglobin": [["L", 13.5, 17.5, R], ["", 12, 16, R]],
    "Amylase": [["", 30, 110, R], ["", 30, 110, R], ["", 30, 110, R], ["", 30, 110, R],
                ["L", 30, 110, R], ["L", 30, 110, R], ["H", 30, 110, R], ["H", 30, 110, R]],
    "Lipase": [["", 15, 60, S], ["", 15, 60, S], ["L", 15, 60, S], ["L", 15, 60, S], ["H", 15, 60, S], ["H", 15, 60, S]],
    "Chloride, Blood": [["", 98, 107, R]] * 14 + [["H", None, None, R]] + [["", 98, 107, R]] * 2 + [["L", None, None, R]]
                       + [["", 98, 107, R], ["A", 98, 107, R]],
    "Creatinine": [["H", 0.6, 1.2, S], ["L", 0.6, 1.2, S], ["H", 0.7, 1.3, R], ["", None, 1.3, R], ["H", None, 1.3, R]],
}
STEPS = {   # lab: the band's steps, each [the results it covers (their indexes), its words]
    "Potassium": [[[0], "3.5–5.1 on report"]],
    "Sodium": [[[1], "135–145 on report"]],
    "Folate": [[[0], "5–20 from settings"], [[1, 2], "3–17 on report"]],
    "Magnesium": [[[0], "1.7–2.4 on report"], [[1], "1.5–2.5 from settings"]],
    "Hemoglobin": [[[0], "13.5–17.5 on report"], [[1], "12–16 on report"]],
    "Amylase": [[list(range(8)), "30–110 on report"]],
    "Lipase": [[list(range(6)), "15–60 from settings"]],
    "Chloride, Blood": [[list(range(14)), "98–107 on report"], [[15, 16], "98–107 on report"], [[18, 19], "98–107 on report"]],
    "Creatinine": [[[0, 1], "0.6–1.2 from settings"], [[2], "0.7–1.3 on report"], [[3, 4], "≤ 1.3 on report"]],
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
    "Chloride, Blood": [("100", None, None), ("104", "Abnormal", None), "98–107"],
    "Creatinine": [("0.5", "Low", ["settings range", "0.6–1.2"]), ("1.4", "High", None), "≤ 1.3"],
}
# the summary's out-of-range rows since then: [value, flag, range, out, of]
OUT_OF_RANGE = {"Sodium": ["133", "Low", "135–145", 1, 1], "Amylase": [">120", "High", "30–110", 4, 4],
                "Lipase": [">70", "High", "15–60 (settings)", 4, 4], "Chloride, Blood": ["104", "Abnormal", "98–107", 2, 4],
                "Creatinine": ["1.4", "High", "≤ 1.3", 2, 3]}

# ---- one-sided ranges (#780): whether a limit includes its edge, kept from each importer that reads it (CSV, PDF,
# FHIR) through storage, the FHIR export and every view. "<5" leaves 5 out of it, "<=5" keeps it; ">60" leaves 60 out,
# ">=60" keeps it. Each lab's reports print one, then the other (two band steps, "< 5" then "≤ 5"), with results at and
# past the edge, some given as bounds (">=5" against "<5" is high, against "<=5" may be inside it). PRINTED: each
# result's range as printed, in CASES' order; EDGES: its edge as kept (pts[7]: "<" the high left out, ">" the low)
UNITS.update({"C-Reactive Protein": "mg/L", "eGFR": "mL/min/1.73m2"})
CASES.update({
    "C-Reactive Protein": [(100, 4, None, 5, "", "4"), (90, 5, None, 5, "", "5"), (80, 5, None, 5, "", ">=5"),
                           (70, 5, None, 5, "", "<5"), (40, 5, None, 5, "", "5"), (30, 6, None, 5, "", "6"),
                           (20, 5, None, 5, "", ">=5"), (10, 5, None, 5, "", ">5")],
    "eGFR": [(100, 61, 60, None, "", "61"), (90, 60, 60, None, "", "60"), (80, 60, 60, None, "", "<=60"),
             (70, 60, 60, None, "", ">60"), (40, 60, 60, None, "", "60"), (30, 59, 60, None, "", "59"),
             (20, 60, 60, None, "", "<=60"), (10, 60, 60, None, "", "<60")],
})
PRINTED = {"C-Reactive Protein": ["<5"] * 4 + ["<=5"] * 4, "eGFR": [">60"] * 4 + [">=60"] * 4}
EDGES = {"C-Reactive Protein": ["<"] * 4 + [""] * 4, "eGFR": [">"] * 4 + [""] * 4}
JUDGED.update({
    "C-Reactive Protein": [["", None, 5, R], ["H", None, 5, R], ["H", None, 5, R], ["", None, 5, R],
                           ["", None, 5, R], ["H", None, 5, R], ["", None, 5, R], ["H", None, 5, R]],
    "eGFR": [["", 60, None, R], ["L", 60, None, R], ["L", 60, None, R], ["", 60, None, R],
             ["", 60, None, R], ["L", 60, None, R], ["", 60, None, R], ["L", 60, None, R]],
})
STEPS.update({"C-Reactive Protein": [[[0, 1, 2, 3], "< 5 on report"], [[4, 5, 6, 7], "≤ 5 on report"]],
              "eGFR": [[[0, 1, 2, 3], "> 60 on report"], [[4, 5, 6, 7], "≥ 60 on report"]]})
TRENDS.update({"C-Reactive Protein": [("<5", None, ["report range", "< 5"]), (">5", "High", None), "≤ 5"],
               "eGFR": [(">60", None, ["report range", "> 60"]), ("<60", "Low", None), "≥ 60"]})
OUT_OF_RANGE.update({"C-Reactive Protein": [">5", "High", "≤ 5", 2, 4], "eGFR": ["<60", "Low", "≥ 60", 2, 4]})
# and in a unit Labs converts (#780 with #782): in SI units "Reported … ref" says its report's range as printed, "< 100"
# then "≤ 100", the drawer's row converted with its series. Not printed for the importers (PRINTED): stored as read
UNITS["Glucose, Blood"] = "mg/dL"
CASES["Glucose, Blood"] = [(100, 99, None, 100, "", "99"), (40, 100, None, 100, "", "100"), (10, 100, None, 100, "", "100")]
EDGES["Glucose, Blood"] = ["<", "<", ""]
JUDGED["Glucose, Blood"] = [["", None, 100, R], ["H", None, 100, R], ["", None, 100, R]]
STEPS["Glucose, Blood"] = [[[0, 1], "< 100 on report"], [[2], "≤ 100 on report"]]
TRENDS["Glucose, Blood"] = [("99", None, ["report range", "< 100"]), ("100", None, None), "≤ 100"]
OUT_OF_RANGE["Glucose, Blood"] = ["100", "High", "< 100", 1, 2]


def edges(lab):
    """Each result's edge as kept (EDGES; "" for every result of the other cases)."""
    return EDGES.get(lab) or [""] * len(CASES[lab])


SAID = {"H": "High", "L": "Low", "A": "Abnormal"}   # a flag as the summary, its PDF and a share link say it (summary.out_of_range)


def words(lo, hi, edge=""):
    """A range as every page, the summary, its PDF and a share link say it (ranges.js rangeWords, summary.range_words):
    "30–110", "< 5", "≤ 5", "> 60", "≥ 60"; "" for none."""
    if lo is not None and hi is not None:
        return f"{lo:g}–{hi:g}"
    if hi is not None:
        return f"{'<' if '<' in edge else '≤'} {hi:g}"
    return f"{'>' if '>' in edge else '≥'} {lo:g}" if lo is not None else ""


def day(n, today=None):
    return ((today or datetime.date.today()) - datetime.timedelta(days=n)).isoformat() + "T08:00:00"


def printed_range(lo, hi, edge=""):
    """A range as a report prints it: "3.5 - 5.1", "<=1.3", ">=60", or ""; a limit that leaves its edge out (edge, #780)
    "<5", ">60", as PRINTED has them (printed())."""
    if lo is not None and hi is not None:
        return f"{lo:g} - {hi:g}"
    if hi is not None:
        return f"{'<' if '<' in edge else '<='}{hi:g}"
    return f"{'>' if '>' in edge else '>='}{lo:g}" if lo is not None else ""


def draw_days():
    """The days back with a lab draw, the oldest first."""
    return sorted({d for pts in CASES.values() for d, *_ in pts}, reverse=True)


def day_results(d):
    """A draw day's results: [(lab, its index in CASES[lab])], in CASES' order."""
    return [(n, i) for n, pts in CASES.items() for i, p in enumerate(pts) if p[0] == d]


def records(today=None):
    """The profile's records, as an import stores them (records.store): a point with an edge keeps it as pts[7]; a lab
    day each draw day, its one result listing that day's rows as the report prints them, and the count of flags its
    importer stored (abn: the report's own)."""
    labs = [{"name": n, "unit": UNITS[n], "panel": "", "n": len(pts), "txt": [],
             "pts": [[day(d, today), v, lo, hi, f, t] + ([None, e] if e else []) for (d, v, lo, hi, f, t), e in zip(pts, edges(n))]}
            for n, pts in CASES.items()]
    draws, details = draw_days(), {}
    events = [{"id": i + 1, "t": day(d, today), "k": "lab", "title": "Lab results (fictional)"} for i, d in enumerate(draws)]
    events.append({"id": len(events) + 1, "t": day(SINCE, today), "k": "visit", "title": "Office visit (fictional)"})   # the summary's "since"
    for i, d in enumerate(draws):
        rows = [[ROW_NAMES.get(n, n), CASES[n][j][5], UNITS[n], printed_range(*CASES[n][j][2:4], edges(n)[j]), CASES[n][j][4]]
                for n, j in day_results(d)]
        abn = {"abn": n} if (n := sum(1 for r in rows if r[4])) else {}
        events[i].update(abn)
        events.append({"id": len(events) + 1, "t": day(d, today), "k": "labitem", "title": "Lab results (fictional)", "hidden": 1, **abn})
        details[str(len(events))] = {"type": "result", "order": "Lab results (fictional)", "rows": rows}
        details[str(i + 1)] = {"type": "labday", "ids": [len(events)]}
    return {"built": day(0, today)[:10], "source": "fictional", "events": events, "details": details, "costs": [], "upcoming": [], "labs": labs}


def share_judged(lab):
    """What a share link says of each result (its report's range and flag only: it never sends a range from Settings)."""
    return [[f, lo, hi, R] if src == R else ["", None, None, R] for f, lo, hi, src in JUDGED[lab]]


def printed(today=None):
    """The one-sided cases as their reports print them, for the importers that read them (#780): [(lab, unit, time,
    value as given, range as printed, index in CASES)], in time order."""
    return sorted(((n, UNITS[n], day(d, today), t, PRINTED[n][i], i) for n in PRINTED for i, (d, *_, t) in enumerate(CASES[n])),
                  key=lambda r: (r[2], r[0]))


# ---------------------------------------------------------------- #781: one result twice, and a lab in two units
# A profile of two imports (copies(1), a spreadsheet; then copies(2), a portal's): the same result in each, its range
# and flag said in one, in the other, or a part in each, before the "since" day (COPIES_SINCE days back, a visit) and
# after it; a lab in two units; and one result in two units (a change of prefix: 11.0 g/dL and 110 g/L), which "Same
# result" can link (crossmatch.match). COPIES: (lab, unit): {import: [(days back, value, low, high, flag, as given)]}.
# Each result's [flag, low, high] (no range from Settings here) as:
#   COPIES_SEEN  Labs shows it before "Same result": a lab in two units two series (#438); a result twice once, as
#                COPIES_ONCE (#825: it showed both copies, the later read alone as the latest, its flag lost)
#   COPIES_ONCE  the summary, its PDF and a share link show it then: a result twice once, with any range and flag either
#                copy has (ranges.copies), each unit its own row
#   COPIES_SAME  every one shows it once each pair is linked as the same result (crossmatch.apply): the older import's
#                copy kept, with the other's range and flag where it has none, a range in another unit converted exactly
COPIES_SINCE = 45
COPIES = {
    # the issue's Potassium: typed in with no range, then a portal's with its report's range and Low
    ("Potassium", "mmol/L"): {1: [(30, 3.2, None, None, "", "3.2"), (5, 4.0, None, None, "", "4.0")],
                              2: [(30, 3.2, 3.5, 5.1, "L", "3.2")]},
    # the reverse: the first import's with its range (Low worked out against it), the portal's with none
    ("Calcium", "mg/dL"): {1: [(20, 8.1, 8.5, 10.5, "", "8.1")], 2: [(20, 8.1, None, None, "", "8.1")]},
    # a flag in one (no range), the range in the other (no flag), before the "since" day: the trend's Then
    ("Sodium", "mmol/L"): {1: [(60, 133, None, None, "L", "133"), (10, 140, 135, 145, "", "140")],
                           2: [(60, 133, 135, 145, "", "133")]},
    # a lab in two units: Low in the first's, the latest in the second's
    ("Glucose, Blood", "mmol/L"): {1: [(100, 5.0, 3.9, 5.5, "", "5.0"), (30, 3.1, 3.9, 5.5, "L", "3.1")]},
    ("Glucose, Blood", "mg/dL"): {2: [(10, 92, 70, 99, "", "92")]},
    # one result in two units, its range and Low in the second's
    ("Hemoglobin", "g/dL"): {1: [(15, 11.0, None, None, "", "11.0")]},
    ("Hemoglobin", "g/L"): {2: [(15, 110, 120, 160, "L", "110")]},
}
COPIES_SEEN = {
    ("Potassium", "mmol/L"): [["", None, None], ["L", 3.5, 5.1], ["", None, None]],
    ("Calcium", "mg/dL"): [["L", 8.5, 10.5], ["", None, None]],
    ("Sodium", "mmol/L"): [["L", None, None], ["L", 135, 145], ["", 135, 145]],
    ("Glucose, Blood", "mmol/L"): [["", 3.9, 5.5], ["L", 3.9, 5.5]],
    ("Glucose, Blood", "mg/dL"): [["", 70, 99]],
    ("Hemoglobin", "g/dL"): [["", None, None]],
    ("Hemoglobin", "g/L"): [["L", 120, 160]],
}
COPIES_ONCE = {**COPIES_SEEN, ("Potassium", "mmol/L"): [["L", 3.5, 5.1], ["", None, None]],
               ("Calcium", "mg/dL"): [["L", 8.5, 10.5]], ("Sodium", "mmol/L"): [["L", 135, 145], ["", 135, 145]]}
COPIES_SAME = {**{k: v for k, v in COPIES_ONCE.items() if k != ("Hemoglobin", "g/L")}, ("Hemoglobin", "g/dL"): [["L", 12, 16]]}
# the summary since then (the key labs COPIES_LABS): each trend row (each unit its own, the latest result's first) its
# Then and Now ([value, flag in words] or None) and its Now's range; and its out-of-range rows [value, flag, range, out, of]
COPIES_LABS = list(dict.fromkeys(n for n, _ in COPIES))
COPIES_TRENDS = {
    ("Potassium", "mmol/L"): [None, ["4.0", None], ""],
    ("Calcium", "mg/dL"): [None, ["8.1", "Low"], "8.5–10.5"],
    ("Sodium", "mmol/L"): [["133", "Low"], ["140", None], "135–145"],
    ("Glucose, Blood", "mg/dL"): [None, ["92", None], "70–99"],
    ("Glucose, Blood", "mmol/L"): [["5.0", None], ["3.1", "Low"], "3.9–5.5"],
    ("Hemoglobin", "g/dL"): [None, ["11.0", None], ""],
    ("Hemoglobin", "g/L"): [None, ["110", "Low"], "120–160"],
}
COPIES_TRENDS_SAME = {**{k: v for k, v in COPIES_TRENDS.items() if k != ("Hemoglobin", "g/L")},
                      ("Hemoglobin", "g/dL"): [None, ["11.0", "Low"], "12–16"]}
COPIES_OUT = {("Potassium", "mmol/L"): ["3.2", "Low", "3.5–5.1", 1, 2], ("Calcium", "mg/dL"): ["8.1", "Low", "8.5–10.5", 1, 1],
              ("Glucose, Blood", "mmol/L"): ["3.1", "Low", "3.9–5.5", 1, 1], ("Hemoglobin", "g/L"): ["110", "Low", "120–160", 1, 1]}
COPIES_OUT_SAME = {**{k: v for k, v in COPIES_OUT.items() if k != ("Hemoglobin", "g/L")},
                   ("Hemoglobin", "g/dL"): ["11.0", "Low", "12–16", 1, 1]}

# ---- copies and one-sided ranges together (#780 with #781): a result's edge goes with its range. Two copies that differ
# in it, a report's "<5" in one and no range in the other: once, with the "<5", 5 left out of it (High; it was "≤ 5" and
# in range). And a one-sided range in another unit, a report's "<40" ng/L: Same result gives the kept 0.04 ng/mL
# "< 0.04" (High), its edge with it. A result with an edge has it as its seventh, and [flag, low, high, edge] in the
# tables; its row in the drawer prints its range as its report did ("<5")
COPIES.update({
    ("C-Reactive Protein", "mg/L"): {1: [(25, 5, None, None, "", "5")], 2: [(25, 5, None, 5, "", "5", "<")]},
    ("Troponin I", "ng/mL"): {1: [(12, 0.04, None, None, "", "0.04")]},
    ("Troponin I", "ng/L"): {2: [(12, 40, None, 40, "", "40", "<")]},
})
COPIES_SEEN.update({("C-Reactive Protein", "mg/L"): [["", None, None], ["H", None, 5, "<"]],
                    ("Troponin I", "ng/mL"): [["", None, None]], ("Troponin I", "ng/L"): [["H", None, 40, "<"]]})
COPIES_ONCE.update({("C-Reactive Protein", "mg/L"): [["H", None, 5, "<"]],
                    ("Troponin I", "ng/mL"): [["", None, None]], ("Troponin I", "ng/L"): [["H", None, 40, "<"]]})
COPIES_SAME.update({("C-Reactive Protein", "mg/L"): [["H", None, 5, "<"]], ("Troponin I", "ng/mL"): [["H", None, 0.04, "<"]]})
COPIES_LABS += ["C-Reactive Protein", "Troponin I"]
COPIES_TRENDS.update({("C-Reactive Protein", "mg/L"): [None, ["5", "High"], "< 5"], ("Troponin I", "ng/mL"): [None, ["0.04", None], ""],
                      ("Troponin I", "ng/L"): [None, ["40", "High"], "< 40"]})
COPIES_TRENDS_SAME.update({("C-Reactive Protein", "mg/L"): [None, ["5", "High"], "< 5"],
                           ("Troponin I", "ng/mL"): [None, ["0.04", "High"], "< 0.04"]})
COPIES_OUT.update({("C-Reactive Protein", "mg/L"): ["5", "High", "< 5", 1, 1], ("Troponin I", "ng/L"): ["40", "High", "< 40", 1, 1]})
COPIES_OUT_SAME.update({("C-Reactive Protein", "mg/L"): ["5", "High", "< 5", 1, 1], ("Troponin I", "ng/mL"): ["0.04", "High", "< 0.04", 1, 1]})
COPIES_LINKED = ["C-Reactive Protein", "Calcium", "Hemoglobin", "Potassium", "Sodium", "Troponin I"]   # what Same result links
# each import's lab days (#782: a lab day each draw day, its rows as its report prints them), by (import, days back):
# each row's lab and its flag as Labs shows its result, which the drawer says and the Timeline's badge counts. Before
# Same result each copy's own; after it the kept one's, the other's rows folded into it and its lab day gone with them
COPIES_DAYS = {
    (1, 100): [("Glucose, Blood", "")], (1, 60): [("Sodium", "L")], (1, 30): [("Potassium", ""), ("Glucose, Blood", "L")],
    (1, 25): [("C-Reactive Protein", "")], (1, 20): [("Calcium", "L")], (1, 15): [("Hemoglobin", "")], (1, 12): [("Troponin I", "")],
    (1, 10): [("Sodium", "")], (1, 5): [("Potassium", "")],
    (2, 60): [("Sodium", "L")], (2, 30): [("Potassium", "L")], (2, 25): [("C-Reactive Protein", "H")], (2, 20): [("Calcium", "")],
    (2, 15): [("Hemoglobin", "L")], (2, 12): [("Troponin I", "H")], (2, 10): [("Glucose, Blood", "")],
}
COPIES_DAYS_SAME = {**{k: v for k, v in COPIES_DAYS.items() if k[0] == 1}, (1, 30): [("Potassium", "L"), ("Glucose, Blood", "L")],
                    (1, 25): [("C-Reactive Protein", "H")], (1, 15): [("Hemoglobin", "L")], (1, 12): [("Troponin I", "H")],
                    (2, 10): [("Glucose, Blood", "")]}
# #825: before Same result too, a result twice is one result, each copy with any range and flag either has
# (crossmatch.copies, as the summary's ranges.copies): Labs shows it once, and each import's lab day flags its row so
COPIES_SEEN = COPIES_ONCE
COPIES_DAYS.update({(1, 30): [("Potassium", "L"), ("Glucose, Blood", "L")], (1, 25): [("C-Reactive Protein", "H")],
                    (2, 20): [("Calcium", "L")]})


def copies(imp, today=None, cases=None):
    """Import imp's records (1 or 2) of cases (COPIES by default, or LATEST), as an import stores them (records.store): its labs, each with its source's key (a
    spreadsheet's, a portal's: what tells a result's copies apart, crossmatch.point_key), a point with an edge keeping it
    as pts[7]; their draws, each a lab day whose one result lists that day's rows as the report prints them (#782); and
    in the first the summary's "since" visit."""
    edge, cases = (lambda e: [None, e[0]] if e and e[0] else []), cases or COPIES
    labs = [{"name": n, "key": f"{('csv', 'fhir')[imp - 1]}:{n.lower()}|{u}", "unit": u, "panel": "", "n": len(by[imp]), "txt": [],
             "pts": [[day(d, today), v, lo, hi, f, t, *edge(e)] for d, v, lo, hi, f, t, *e in by[imp]]} for (n, u), by in cases.items() if imp in by]
    draws = sorted({d for by in cases.values() for d, *_ in by.get(imp, ())}, reverse=True)
    events = [{"id": i + 1, "t": day(d, today), "k": "lab", "title": f"Lab results (fictional, {imp})"} for i, d in enumerate(draws)]
    if imp == 1:
        events.append({"id": len(events) + 1, "t": day(COPIES_SINCE, today), "k": "visit", "title": "Office visit (fictional)"})
    details = {}
    for i, d in enumerate(draws):
        rows = [[n, t, u, printed_range(lo, hi, (e or [""])[0]), f] for (n, u), by in cases.items() for dd, v, lo, hi, f, t, *e in by.get(imp, ()) if dd == d]
        abn = {"abn": n} if (n := sum(1 for r in rows if r[4])) else {}
        events[i].update(abn)
        events.append({"id": len(events) + 1, "t": day(d, today), "k": "labitem", "title": f"Lab results (fictional, {imp})", "hidden": 1, **abn})
        details[str(len(events))] = {"type": "result", "order": f"Lab results (fictional, {imp})", "rows": rows}
        details[str(i + 1)] = {"type": "labday", "ids": [len(events)]}
    return {"built": day(0, today)[:10], "source": "fictional", "events": events, "details": details, "costs": [], "upcoming": [], "labs": labs}


# ---------------------------------------------------------------- #825: one result twice at one time, its latest
# A profile of two imports (copies(1, cases=LATEST), then copies(2, ...)): each lab's history in the first (nineteen
# results inside its report's range: with its latest, a row of the heatmap) and its latest result in both, at one time,
# a range on one copy only, in each import order: Potassium's on the first's copy (the second's, the later, was read
# alone as the latest, and its High lost), Sodium's on the second's, with its report's H. Every view reads the latest
# as one result with that range and flag (crossmatch.copies, as the summary's ranges.copies): the Overview's tile (▲
# High, its change from the result before it, never "no change" from its own copy) and its list of what's outside its
# range; Labs' chart (one mark at that time, a ▲, never under a dot) and its table (one row); the heatmap (one cell,
# counted once); and the drawer (each import's lab day flags its row). LATEST: as COPIES; LATEST_JUDGED each result's
# [flag, low, high] on Labs; LATEST_TILES the tile's [flag, value, change]; LATEST_OUT the Overview's list's [value
# with its flag, range]; LATEST_DAYS each import's lab day of the latest, (import, days back): its rows' labs and flags
LATEST = {
    ("Potassium", "mmol/L"): {1: [(200 - 10 * i, v, 3.5, 5.1, "", f"{v:.1f}") for i in range(19) for v in [round(3.8 + i % 5 * .2, 1)]]
                              + [(2, 5.2, 3.5, 5.1, "", "5.2")],
                              2: [(2, 5.2, None, None, "", "5.2")]},
    ("Sodium", "mmol/L"): {1: [(200 - 10 * i, 138 + i % 5, 135, 145, "", str(138 + i % 5)) for i in range(19)] + [(2, 150, None, None, "", "150")],
                           2: [(2, 150, 135, 145, "H", "150")]},
}
LATEST_LABS = [n for n, _ in LATEST]
LATEST_JUDGED = {"Potassium": [["", 3.5, 5.1]] * 19 + [["H", 3.5, 5.1]], "Sodium": [["", 135, 145]] * 19 + [["H", 135, 145]]}
LATEST_TILES = {"Potassium": ["▲ High", "5.2", "↑ 0.8"], "Sodium": ["▲ High", "150", "↑ 9"]}
LATEST_OUT = {"Potassium": ["5.2 mmol/L ▲ High", "ref 3.5–5.1"], "Sodium": ["150 mmol/L ▲ High", "ref 135–145"]}
LATEST_DAYS = {(1, 2): [("Potassium", "H"), ("Sodium", "H")], (2, 2): [("Potassium", "H"), ("Sodium", "H")]}

# ---- #833: the same profile once each latest pair is marked "Not the same" (POST .../same, same false): two results,
# each with its own range and flag, everywhere. Labs shows both (at one time the unflagged first, the flagged after it,
# so its ▲ is never under a dot and what reads the latest reads it); the Overview's tile the flagged one, its change from
# the result before that time (never from the other at it), and its list it; the heatmap two cells that day, counted
# once; each import's lab day its own row's flag; the summary (since NOT_SAME_SINCE days back) and a share link both, a
# trend's Now the flagged one. NOT_SAME_JUDGED: Labs' [flag, low, high]; NOT_SAME_DAYS: each import's lab day of the
# latest; NOT_SAME_OUT: the summary's out-of-range rows [value, flag, range, out, of]; NOT_SAME_TRENDS: each trend's
# [Then, Now, Now's flag]. The Overview's tiles and list are LATEST_TILES and LATEST_OUT
NOT_SAME_JUDGED = {"Potassium": [["", 3.5, 5.1]] * 19 + [["", None, None], ["H", 3.5, 5.1]],
                   "Sodium": [["", 135, 145]] * 19 + [["", None, None], ["H", 135, 145]]}
NOT_SAME_DAYS = {(1, 2): [("Potassium", "H"), ("Sodium", "")], (2, 2): [("Potassium", ""), ("Sodium", "H")]}
NOT_SAME_SINCE = 15
NOT_SAME_OUT = {"Potassium": ["5.2", "High", "3.5–5.1", 1, 2], "Sodium": ["150", "High", "135–145", 1, 2]}
NOT_SAME_TRENDS = {"Potassium": ["4.4", "5.2", "High"], "Sodium": ["141", "150", "High"]}

# ---- #834: each import's lab day of LATEST's latest, the Ref range of each row in the drawer: its report's as printed,
# else (its report printed none) the range its result has from its other copy, said where it came from; once marked
# "Not the same", its own (none)
COPY_RANGE = " (from the other copy's report)"
LATEST_DAY_RANGES = {(1, 2): ["3.5 - 5.1", "135–145" + COPY_RANGE], (2, 2): ["3.5–5.1" + COPY_RANGE, "135 - 145"]}
NOT_SAME_DAY_RANGES = {(1, 2): ["3.5 - 5.1", ""], (2, 2): ["", "135 - 145"]}
