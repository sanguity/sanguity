"""The matrix of #752: one profile's fictional results that every page judges, draws and says the same way, for
tests/test_ranges.py (the server: the visit summary, its PDF, share links) and tests/browser_check.py (the page: Labs,
the Overview, Compare, a share link's page, the summary's preview and its PDF; #782: the Timeline's counts, the drawer's
rows, the heatmap and a converted value's "Reported … ref").

Each case is a lab whose results [days back, value, low, high, flag, as given] go through one of the ways a result's
range and flag were said differently: a report's range then none; none then a report's; a range from Settings then a
report's; a report's then one from Settings; a range that changes; values given as "<" and ">" bounds at and past
each edge of a report's range and of one from Settings; and one-sided ranges that leave their edge out ("<5", ">60") or
keep it ("<=5", ">=60"), at and past it, through the importers that read them too (#780); and a report's flags that
say no way ("A", "Abnormal", "Critical"), inside its range, past it and with none printed, read through the CSV importer
as an upload is (THROUGH_CSV: ALT, its flags in CASES as printed, #817), and pasted into Type them in (PASTE_FLAGS,
#835); and ranges given as text alone, "a - b", "<40", "<=40", "less than 40", read through the C-CDA importer
(THROUGH_CCDA: AST, its texts RANGE_TEXT, #827), and through CareSpace and MyChart exports (#836); and flags stored as
their reports printed them, as older imports stored them ("N", ">", "HH", "Normal": Calcium, #839). JUDGED is what each
result is judged as, by the one rule (server/ranges.py judge, www/js/ranges.js applyRangeOverrides): [flag, low, high,
source], and EDGES its edge where its range has one; STEPS the band's steps, each the results it covers, with its words
(a result whose report gave no range is under none). The summary's "since" day is SINCE days back, so each case has a
Then and a Now. Each draw day is a lab day with one result listing that day's results as a report prints them (#782):
its row named as the report names the test (ROW_NAMES: "CHLORIDE" for the series "Chloride, Blood", "CREAT" for
"Creatinine"), the value as given, the range as printed (a one-sided one with its edge, "<5" or "<=5") and the report's
own flag, and the count of flags its importer stored. COPIES (#781, at the end) is a second profile's: one result
twice (from two imports) and a lab in two units, before and after "Same result"; UPLOADED (#846) one result twice
through real uploads, a spreadsheet's and a C-CDA document's, put through the import's preview and save in each
order. Fictional data only.
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
    "Chloride, Blood": [("100", None, None), ("104", "Flagged", None), "98–107"],
    "Creatinine": [("0.5", "Low", ["settings range", "0.6–1.2"]), ("1.4", "High", None), "≤ 1.3"],
}
# the summary's out-of-range rows since then: [value, flag, range, out, of]
OUT_OF_RANGE = {"Sodium": ["133", "Low", "135–145", 1, 1], "Amylase": [">120", "High", "30–110", 4, 4],
                "Lipase": [">70", "High", "15–60 (settings)", 4, 4], "Chloride, Blood": ["104", "Flagged", "98–107", 2, 4],
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

# ---- a report's own flag (#817): ALT's results come in a spreadsheet with its report's flags in a Flag column, read by
# the CSV importer (THROUGH_CSV: records() takes their series from csv_labs.parse, as an upload would, each flag in
# CASES as printed). "A", "Abnormal", "Critical" and "AA" say no way: placed by the range where it can ("A" on 60
# against 7–56 is High, "Abnormal" on 5 Low), else kept "A", ◆ in every view (inside its range, or with none printed;
# the CSV and FHIR importers kept it with no flag, so Labs, the heatmap, the Overview, the summary and share links said
# it in range); "N" says neither, so the range alone (in range)
UNITS["ALT"] = "U/L"
CASES["ALT"] = [(100, 30, 7, 56, "", "30"), (90, 40, 7, 56, "A", "40"), (80, 60, 7, 56, "A", "60"), (70, 5, 7, 56, "Abnormal", "5"),
                (60, 50, 7, 56, "Abnormal", "50"), (40, 45, None, None, "Critical", "45"), (30, 35, 7, 56, "N", "35"),
                (20, 25, 7, 56, "AA", "25"), (10, 20, 7, 56, "A", "20")]
THROUGH_CSV = ["ALT"]
JUDGED["ALT"] = [["", 7, 56, R], ["A", 7, 56, R], ["H", 7, 56, R], ["L", 7, 56, R], ["A", 7, 56, R], ["A", None, None, R],
                 ["", 7, 56, R], ["A", 7, 56, R], ["A", 7, 56, R]]
# and pasted into Type them in (#835: pasted() as a spreadsheet's copy, the flags in an "Abnormal" column, some as
# yes or no), judged as JUDGED: the paste kept H and L only, so its "A", "Abnormal", "Critical" and "Yes" were none
PASTE_FLAGS = {"ALT": ["", "Yes", "A", "Abnormal", "Y", "Critical", "No", "AA", "A"]}
STEPS["ALT"] = [[[0, 1, 2, 3, 4], "7–56 on report"], [[6, 7, 8], "7–56 on report"]]
TRENDS["ALT"] = [("50", "Flagged", None), ("20", "Flagged", None), "7–56"]
OUT_OF_RANGE["ALT"] = ["20", "Flagged", "7–56", 3, 4]

# ---- a range given as text alone (#827): AST's results come in a C-CDA document whose reference ranges are text with
# no IVL (RANGE_TEXT), read by the C-CDA importer (THROUGH_CCDA: records() takes their series from ccda.parse, as an
# upload would). It read a range from an IVL, or a text "a - b", so "<40" was dropped: no range and no flag on Labs while
# the drawer showed "<40" beside the value. Now through the one range parser, its edge kept: 40 against "<40" (or "less
# than 40") is High, against "<=40" in range
UNITS["AST"] = "U/L"
CASES["AST"] = [(100, 30, 10, 40, "", "30"), (90, 8, 10, 40, "", "8"), (80, 39, None, 40, "", "39"), (70, 40, None, 40, "", "40"),
                (40, 40, None, 40, "", "40"), (30, 41, None, 40, "", "41"), (20, 40, None, 40, "", "40"), (10, 35, None, 40, "", "35")]
RANGE_TEXT = {"AST": ["10 - 40", "10-40", "<40", "< 40", "<=40", "≤ 40", "<40 U/L", "less than 40"]}
THROUGH_CCDA = ["AST"]
# the same results from a CareSpace export and an Epic MyChart export, each range its displayed text alone, no low or
# high of its own (#836: carespace() and epic(), which tests/test_ranges.py reads through those importers): dropped, as
# the C-CDA importer's were
EDGES["AST"] = ["", "", "<", "<", "", "", "<", "<"]
JUDGED["AST"] = [["", 10, 40, R], ["L", 10, 40, R], ["", None, 40, R], ["H", None, 40, R], ["", None, 40, R], ["H", None, 40, R],
                 ["H", None, 40, R], ["", None, 40, R]]
STEPS["AST"] = [[[0, 1], "10–40 on report"], [[2, 3], "< 40 on report"], [[4, 5], "≤ 40 on report"], [[6, 7], "< 40 on report"]]
TRENDS["AST"] = [("40", "High", None), ("35", None, None), "< 40"]
OUT_OF_RANGE["AST"] = ["40", "High", "< 40", 2, 4]

# ---- a flag stored as its report printed it (#839): Calcium's results as an older import stored them, before #817's one
# rule, each flag in CASES as printed ("N", ">", "<", "HH", "LL", "AA", "Normal"), stored as it is. Every view reads a
# stored flag as a new import of it would (server/ranges.py read_flag, ranges.js readFlag: the one rule for a printed
# flag, common.report_flag): ">" and "HH" High and "<" and "LL" Low whatever the range; "AA" placed by the range where it
# can be, else kept "A"; "N" and "Normal" say neither, so the range alone ("N" on 10.9 against 8.5–10.5 is High), and a
# "Normal" with no range printed takes a range from Settings (8.6–10.3) as a result with no flag does. The page read
# each "N", ">", "<" and "Normal" as ◆ Flagged, while the summary and share links read "N" and "Normal" as no flag, ">"
# as High, and "AA" as Flagged past its range
UNITS["Calcium"] = "mg/dL"
OVERRIDES.append({"lab": "Calcium", "unit": "mg/dL", "sex": "any", "low": 8.6, "high": 10.3})
CASES["Calcium"] = [(100, 9.4, 8.5, 10.5, "N", "9.4"), (90, 9.6, 8.5, 10.5, ">", "9.6"), (80, 9.0, 8.5, 10.5, "<", "9.0"),
                    (70, 11.8, 8.5, 10.5, "HH", "11.8"), (60, 9.8, 8.5, 10.5, "Normal", "9.8"), (50, 7.6, 8.5, 10.5, "LL", "7.6"),
                    (40, 9.9, 8.5, 10.5, "AA", "9.9"), (30, 11.2, 8.5, 10.5, "AA", "11.2"), (20, 10.4, None, None, "Normal", "10.4"),
                    (10, 10.9, 8.5, 10.5, "N", "10.9")]
JUDGED["Calcium"] = [["", 8.5, 10.5, R], ["H", 8.5, 10.5, R], ["L", 8.5, 10.5, R], ["H", 8.5, 10.5, R], ["", 8.5, 10.5, R],
                     ["L", 8.5, 10.5, R], ["A", 8.5, 10.5, R], ["H", 8.5, 10.5, R], ["H", 8.6, 10.3, S], ["H", 8.5, 10.5, R]]
STEPS["Calcium"] = [[list(range(8)), "8.5–10.5 on report"], [[8], "8.6–10.3 from settings"], [[9], "8.5–10.5 on report"]]
TRENDS["Calcium"] = [("7.6", "Low", None), ("10.9", "High", None), "8.5–10.5"]
OUT_OF_RANGE["Calcium"] = ["10.9", "High", "8.5–10.5", 4, 4]


def edges(lab):
    """Each result's edge as kept (EDGES; "" for every result of the other cases)."""
    return EDGES.get(lab) or [""] * len(CASES[lab])


SAID = {"H": "High", "L": "Low", "A": "Flagged"}   # a flag as the summary, its PDF and a share link say it (summary.out_of_range)
# #819: flagged results said one way. A ◆ ("A", Chloride, Blood's latest, inside its range) is "Flagged" everywhere (SAID;
# it was "Abnormal" in the summary and share links), and every heading, count and note of flagged results says it where
# there's one: FLAGGED_WORDS, each form's words (server/ranges.py flagged_words, ranges.js flaggedWords) without a ◆ and
# with one; FLAGGED_NOTE, the summary's note of what "Flagged" is, said where there's one
FLAGGED_WORDS = {"its": ("outside its reference range", "outside its reference range or flagged on its report"),
                 "count": ("outside reference range", "outside reference range or flagged on their report"),
                 "short": ("out of range", "out of range or flagged"), "head": ("Outside", "Outside or flagged")}
FLAGGED_NOTE = "Flagged: flagged on its own report without saying high or low, its value inside its range or with none given."


def flagged(flags, form="count"):
    """FLAGGED_WORDS' words of a form for results flagged so ("H", "L", "A")."""
    return FLAGGED_WORDS[form]["A" in flags]


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


def through_csv(today=None):
    """THROUGH_CSV's series as the CSV importer reads them (csv_labs.parse, #817), from a spreadsheet of their results
    as their reports print them: date, time, test, value as given, unit, range as printed and flag as printed."""
    import os, tempfile
    from server.importers import csv_labs
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "flags.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Date,Time,Test,Value,Unit,Reference range,Flag\n" + "".join(
                f"{day(d, today)[:10]},{day(d, today)[11:16]},{n},{t},{UNITS[n]},{printed_range(lo, hi)},{flag}\n"
                for n in THROUGH_CSV for d, v, lo, hi, flag, t in CASES[n]))
        return {s["name"]: s for s in csv_labs.parse({"flags.csv": path})["data"]["labs"]}


def pasted(lab, today=None):
    """lab's results as a spreadsheet's copy pasted into Type them in (#835): tab-separated, a header row, each flag as
    PASTE_FLAGS has it in an "Abnormal" column."""
    return "Date\tTime\tTest\tValue\tUnit\tReference range\tAbnormal\n" + "".join(
        f"{day(d, today)[:10]}\t{day(d, today)[11:16]}\t{lab}\t{t}\t{UNITS[lab]}\t{printed_range(lo, hi)}\t{f}\n"
        for (d, v, lo, hi, _, t), f in zip(CASES[lab], PASTE_FLAGS[lab]))


def ccda(rows):
    """A fictional C-CDA document of lab results, each [(test, unit, time, value as given, range as text)] an
    observation of its own, its reference range its text alone (no IVL, #827); a value given as a bound ("<5") as text."""
    from html import escape
    from server.importers import common
    obs = []
    for n, u, t, v, r in rows:
        loinc = common.loinc_of(common.canonical_lab(n))
        code = f'code="{loinc}" codeSystem="2.16.840.1.113883.6.1" ' if loinc else ""
        value = (f'<value xsi:type="ST">{escape(v)}</value>' if v[:1] in "<>" else f'<value xsi:type="PQ" value="{v}" unit="{escape(u)}"/>')
        obs.append(f'<component><observation classCode="OBS" moodCode="EVN"><code {code}displayName="{escape(n)}"/>'
                   f'<statusCode code="completed"/><effectiveTime value="{t[:19].replace("-", "").replace("T", "").replace(":", "")}"/>{value}'
                   f'<referenceRange><observationRange><text>{escape(r)}</text></observationRange></referenceRange></observation></component>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!-- Fictional test data. -->\n'
            '<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<templateId root="2.16.840.1.113883.10.20.22.1.1"/><code code="34133-9" codeSystem="2.16.840.1.113883.6.1"/>'
            '<title>Health Summary (fictional)</title><component><structuredBody><component><section>'
            '<templateId root="2.16.840.1.113883.10.20.22.2.3.1"/><code code="30954-2" codeSystem="2.16.840.1.113883.6.1"/>'
            '<entry><organizer classCode="BATTERY" moodCode="EVN"><code displayName="Lab results (fictional)"/>'
            + "".join(obs) + '</organizer></entry></section></component></structuredBody></component></ClinicalDocument>\n')


def text_ranged(lab, today=None):
    """lab's results with their ranges as text alone (RANGE_TEXT): [(test, unit, time, value as given, range as text)]."""
    return [(lab, UNITS[lab], day(d, today), t, r) for (d, *_, t), r in zip(CASES[lab], RANGE_TEXT[lab])]


def carespace(rows, into):
    """A fictional CareSpace export in the folder into, its lab results [(test, unit, time, value as given, range as
    text)] each with its range as displayed alone, no lower or upper limit (#836). -> the importer's files"""
    import json, os
    os.makedirs(os.path.join(into, "api"), exist_ok=True)
    docs = {"lab_results.json": [{"id": f"m{i}", "name": n, "value": {"value": v, "unit": u}, "referenceRange": {"unit": u},
                                  "referenceRangeDisplay": r, "interpretationCode": None, "observationDateTz": t, "labPanelRecordId": "p1"}
                                 for i, (n, u, t, v, r) in enumerate(rows)],
            "lab_panels.json": [{"id": "p1", "testName": "Matrix panel (fictional)"}]}
    for name, data in docs.items():
        with open(os.path.join(into, "api", name), "w", encoding="utf-8") as f:
            json.dump({"data": data}, f)
    return {f"api/{name}": os.path.join(into, "api", name) for name in docs}


def epic(rows, into):
    """A fictional Epic MyChart export's test results in the folder into, each of rows [(test, unit, time, value as
    given, range as text)] a result of its own with its range as formatted alone, no low or high (#836). -> the
    importer's files"""
    import json, os
    os.makedirs(os.path.join(into, "test-results"), exist_ok=True)
    num = lambda v: float(v.lstrip("<>="))
    details = {f"r{i}": {"orderName": "Matrix panel (fictional)", "results": [{
        "name": "Matrix panel (fictional)", "orderMetadata": {"prioritizedInstantISO": t, "resultType": "LAB"},
        "resultComponents": [{"componentInfo": {"name": n, "units": u},
                              "componentResultInfo": {"value": v, "numericValue": num(v), "referenceRange": {"formattedReferenceRange": r}}}]}]}
               for i, (n, u, t, v, r) in enumerate(rows)}
    path = os.path.join(into, "test-results", "matrix_test_results_raw.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"groups": [{"resultList": list(details)}], "details": details}, f)
    return {"test-results/matrix_test_results_raw.json": path}


def through_ccda(today=None):
    """THROUGH_CCDA's series as the C-CDA importer reads them (ccda.parse, #827), from a document of their results with
    each reference range as its text alone (RANGE_TEXT)."""
    import os, tempfile
    from server.importers import ccda as ccda_importer
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ranges.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(ccda([row for n in THROUGH_CCDA for row in text_ranged(n, today)]))
        return {s["name"]: s for s in ccda_importer.parse({"ranges.xml": path})["data"]["labs"]}


def records(today=None):
    """The profile's records, as an import stores them (records.store): a point with an edge keeps it as pts[7],
    THROUGH_CSV's series as the CSV importer reads them (through_csv()) and THROUGH_CCDA's as the C-CDA importer does
    (through_ccda()); a lab day each draw day, its one result listing that day's rows as the report prints them, each
    with the flag its importer stored, and the count of those (abn)."""
    read = {**through_csv(today), **through_ccda(today)}
    labs = [read[n] if n in THROUGH_CSV + THROUGH_CCDA else {"name": n, "unit": UNITS[n], "panel": "", "n": len(pts), "txt": [],
             "pts": [[day(d, today), v, lo, hi, f, t] + ([None, e] if e else []) for (d, v, lo, hi, f, t), e in zip(pts, edges(n))]}
            for n, pts in CASES.items()]
    stored = {s["name"]: [p[4] for p in s["pts"]] for s in labs}
    draws, details = draw_days(), {}
    events = [{"id": i + 1, "t": day(d, today), "k": "lab", "title": "Lab results (fictional)"} for i, d in enumerate(draws)]
    events.append({"id": len(events) + 1, "t": day(SINCE, today), "k": "visit", "title": "Office visit (fictional)"})   # the summary's "since"
    for i, d in enumerate(draws):
        rows = [[ROW_NAMES.get(n, n), CASES[n][j][5], UNITS[n], printed_range(*CASES[n][j][2:4], edges(n)[j]), stored[n][j]]
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

# ---- copies and a report's flag that says no way together (#825 with #817): one copy's "A" with no range printed (kept
# "A" by the one rule, as a spreadsheet's Flag column, a paste or a PDF gives it), the other's report's range, in each
# import order (ALT's "A" in the first import, AST's in the second). Once, the "A" is placed by the range it then has, as
# the one rule places a printed "A": 60 is High against 7–56 in either order (it was ◆ Flagged where the "A" came
# first, its copy's High lost, and ▲ High where it came second), 30 inside it stays ◆ Flagged; after Same result the
# same. ◆ is said "Flagged" in the summary and share links (#819)
COPIES.update({
    ("ALT", "U/L"): {1: [(35, 60, None, None, "A", "60"), (33, 30, None, None, "A", "30")], 2: [(35, 60, 7, 56, "H", "60"), (33, 30, 7, 56, "", "30")]},
    ("AST", "U/L"): {1: [(35, 60, 10, 40, "H", "60"), (33, 30, 10, 40, "", "30")], 2: [(35, 60, None, None, "A", "60"), (33, 30, None, None, "A", "30")]},
})
COPIES_ONCE.update({("ALT", "U/L"): [["H", 7, 56], ["A", 7, 56]], ("AST", "U/L"): [["H", 10, 40], ["A", 10, 40]]})
COPIES_SAME.update({("ALT", "U/L"): [["H", 7, 56], ["A", 7, 56]], ("AST", "U/L"): [["H", 10, 40], ["A", 10, 40]]})
COPIES_LABS += ["ALT", "AST"]
COPIES_TRENDS.update({("ALT", "U/L"): [None, ["30", "Flagged"], "7–56"], ("AST", "U/L"): [None, ["30", "Flagged"], "10–40"]})
COPIES_TRENDS_SAME.update({("ALT", "U/L"): [None, ["30", "Flagged"], "7–56"], ("AST", "U/L"): [None, ["30", "Flagged"], "10–40"]})
COPIES_OUT.update({("ALT", "U/L"): ["30", "Flagged", "7–56", 2, 2], ("AST", "U/L"): ["30", "Flagged", "10–40", 2, 2]})
COPIES_OUT_SAME.update({("ALT", "U/L"): ["30", "Flagged", "7–56", 2, 2], ("AST", "U/L"): ["30", "Flagged", "10–40", 2, 2]})
COPIES_LINKED = sorted(COPIES_LINKED + ["ALT", "ALT", "AST", "AST"])
COPIES_DAYS.update({(i, d): [("ALT", f), ("AST", f)] for i in (1, 2) for d, f in ((35, "H"), (33, "A"))})
COPIES_DAYS_SAME.update({(1, d): [("ALT", f), ("AST", f)] for d, f in ((35, "H"), (33, "A"))})


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


# ---------------------------------------------------------------- #846: one result twice through real uploads
# A profile's two uploads of the same results at one time (UPLOADED_BACK days back): a spreadsheet and a C-CDA document
# (uploaded()), each read by its importer and put through the import's preview and save, in each order (UPLOAD_ORDERS).
# Their reports say a result's range and flag in one file only: the C-CDA's range (an edge with it, "<100"), the
# spreadsheet's "A" with no range. The preview counted a result here already a duplicate by its test, time and value,
# and left the second upload's copy out with its range and flag: the spreadsheet first, Labs, the Overview, the summary
# and share links said each unflagged while the C-CDA's lab day said "Out of range 3"; the C-CDA first, the
# spreadsheet's lab day said "Out of range: None". Now its copy is added with them, and its review says so: each is one
# result with them everywhere (crossmatch.copies, #825), each file's lab day flagging its row. UPLOADED: a lab as its
# reports print it, (unit, value as given, the spreadsheet's range and flag, the C-CDA's range); UPLOADED_SEEN each
# result's [flag, low, high(, edge)] as Labs, the summary and a share link show it; UPLOADED_ROWS each file's lab day's
# rows (the test as printed, the flag Labs shows for its result); UPLOADED_TRENDS and UPLOADED_OUT the summary's since
# UPLOADED_SINCE days back (the key labs UPLOADED_LABS); UPLOADED_HERE the second upload's review: its results already
# here that it adds again with its report's range and flag ("copies"), and those it skips ("results")
UPLOADED_BACK, UPLOADED_SINCE = 3, 10
UPLOADED = {"Sodium": ("mmol/L", "150", "", "", "135-145"), "Potassium": ("mmol/L", "5.2", "", "", "3.5-5.1"),
            "Glucose": ("mg/dL", "100", "", "", "<100"), "ALT": ("U/L", "60", "", "A", "")}
UPLOADED_SEEN = {("Sodium, Blood", "mmol/L"): [["H", 135, 145]], ("Potassium, Blood", "mmol/L"): [["H", 3.5, 5.1]],
                 ("Glucose, Blood", "mg/dL"): [["H", None, 100, "<"]], ("ALT", "U/L"): [["A", None, None]]}
UPLOADED_ROWS = {"csv": [("ALT", "A"), ("Glucose", "H"), ("Potassium", "H"), ("Sodium", "H")],
                 "ccda": [("ALT", "A"), ("Glucose", "H"), ("Potassium", "H"), ("Sodium", "H")]}
UPLOADED_LABS = ["ALT", "Glucose, Blood", "Potassium, Blood", "Sodium, Blood"]
UPLOADED_TRENDS = {("ALT", "U/L"): [None, ["60", "Flagged"], ""], ("Glucose, Blood", "mg/dL"): [None, ["100", "High"], "< 100"],
                   ("Potassium, Blood", "mmol/L"): [None, ["5.2", "High"], "3.5–5.1"],
                   ("Sodium, Blood", "mmol/L"): [None, ["150", "High"], "135–145"]}
UPLOADED_OUT = {("ALT", "U/L"): ["60", "Flagged", "", 1, 1], ("Glucose, Blood", "mg/dL"): ["100", "High", "< 100", 1, 1],
                ("Potassium, Blood", "mmol/L"): ["5.2", "High", "3.5–5.1", 1, 1],
                ("Sodium, Blood", "mmol/L"): ["150", "High", "135–145", 1, 1]}
UPLOADED_HERE = {"copies": 4, "results": 0}
UPLOAD_ORDERS = (("csv", "ccda"), ("ccda", "csv"))
SHOWN = {"Potassium, Blood": "Potassium", "Sodium, Blood": "Sodium"}   # as the summary names them (Settings' labNicknames)


def uploaded(kind, today=None):
    """UPLOADED's results as one file of kind "csv" (a spreadsheet: its range and flag as printed) or "ccda" (a C-CDA
    document, ccda(): its range as text): (its name, its text), the tests in UPLOADED_ROWS' order."""
    t, rows = day(UPLOADED_BACK, today), sorted(UPLOADED.items(), key=lambda x: x[0].lower())
    if kind == "csv":
        return "copies.csv", "Date,Time,Test,Value,Unit,Reference range,Flag\n" + "".join(
            f"{t[:10]},{t[11:16]},{n},{v},{u},{r},{f}\n" for n, (u, v, r, f, _) in rows)
    return "copies.xml", ccda([(n, u, t, v, r) for n, (u, v, _, _, r) in rows])


def uploaded_days(order):
    """Each file's lab day's rows (UPLOADED_ROWS) by (import: 1 the first uploaded, in order; days back)."""
    return {(order.index(k) + 1, UPLOADED_BACK): rows for k, rows in UPLOADED_ROWS.items()}
