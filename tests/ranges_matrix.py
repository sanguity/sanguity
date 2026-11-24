"""The matrix of #752: one profile's fictional results that every page judges, draws and says the same way, for
tests/test_ranges.py (the server: the visit summary, its PDF, share links) and tests/browser_check.py (the page: Labs,
the Overview, Compare, a share link's page, the summary's preview and its PDF; #782: the Timeline's counts, the drawer's
rows, the heatmap and a converted value's "Reported … ref").

Each case is a lab whose results [days back, value, low, high, flag, as given] go through one of the ways a result's
range and flag were said differently: a report's range then none; none then a report's; a range from Settings then a
report's; a report's then one from Settings; a range that changes; values given as "<" and ">" bounds at and past
each edge of a report's range and of one from Settings; and one-sided ranges that leave their edge out ("<5", ">60") or
keep it ("<=5", ">=60"), at and past it, through the importers that read them too (#780), each spelled as reports
print it ("=>60", "< OR = 5", "less than or equal to 5") and read through the CSV importer as an upload is (#850; and
SPELLINGS, the ways a report prints a range, through every importer, Type them in and the FHIR export; COMMAS, "0,500"
and the like in each mode of decimal commas, #859, and in scientific notation, through every importer that reads a
printed value and the FHIR export read back, #870; BOUND_VALUES, a value given as a bound, "=<5", "≥60", the same way,
#870); and a report's flags that say no way ("A", "Abnormal",
"Critical"), inside its range, past it and with none printed, read through the CSV importer as an upload is
(THROUGH_CSV: ALT, its flags in CASES as printed, #817), and pasted into Type them in (PASTE_FLAGS, #835); and ranges
given as text alone, "a - b", "<40", "<=40", "less than 40", read through the C-CDA importer (THROUGH_CCDA: AST, its
texts RANGE_TEXT, #827), and through CareSpace and MyChart exports (#836); and flags stored as their reports printed
them, as older imports stored them ("N", ">", "HH", "Normal": Calcium, #839). JUDGED is what each result is judged
as, by the one rule (server/ranges.py judge, www/js/ranges.js applyRangeOverrides): [flag, low, high, source], and
EDGES its edge where its range has one; STEPS the band's steps, each the results it covers, with its words (a result
whose report gave no range is under none). The summary's "since" day is SINCE days back, so each case has a Then and
a Now. Each draw day is a lab day with one result listing that day's results as a report prints them (#782):
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
# #850: each spelled as reports print it, the matrix's through the CSV importer (THROUGH_CSV below): "=>60" was read
# as a high of 60 (59 against it in range, 60 High), "< OR = 5", "> OR = 60" and "less than or equal to 5" not at all
PRINTED = {"C-Reactive Protein": ["<5", "< 5", "less than 5", "under 5", "<=5", "< OR = 5", "less than or equal to 5", "=<5"],
           "eGFR": [">60", "> 60", "greater than 60", "over 60", ">=60", "=>60", "> OR = 60", "greater than or equal to 60"]}
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
# (#850: and the one-sided cases, each range spelled as PRINTED has it: the one parser of a range as text's reading)
THROUGH_CSV = ["ALT", "C-Reactive Protein", "eGFR"]
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
# than 40") is High, against "<=40" in range; "10,0 - 40,0" with decimal commas (#850: dropped, so 8 wasn't Low)
UNITS["AST"] = "U/L"
CASES["AST"] = [(100, 30, 10, 40, "", "30"), (90, 8, 10, 40, "", "8"), (80, 39, None, 40, "", "39"), (70, 40, None, 40, "", "40"),
                (40, 40, None, 40, "", "40"), (30, 41, None, 40, "", "41"), (20, 40, None, 40, "", "40"), (10, 35, None, 40, "", "35")]
RANGE_TEXT = {"AST": ["10 - 40", "10,0 - 40,0", "<40", "< 40", "<=40", "≤ 40", "<40 U/L", "less than 40"]}
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

# ---- #850: one parser of a range as text (common.range_text) for every importer, Type them in and the FHIR export.
# SPELLINGS: the ways a report prints a range, each [as printed, value, low, high, edge, flag], a value at or past its
# limit ("<5" leaves 5 out of it, so 5 is High; against "<=5" in range). Through a spreadsheet (comma-delimited, and
# with semicolons and decimal commas), Type them in, a PDF report, a C-CDA document, FHIR, CareSpace and MyChart, and
# a FHIR export read back, each gives that range, edge and flag (spelled(); tests/test_ranges.py
# test_2_range_spellings). The CSV importer read "=>60" as a high of 60 (90 against it High) and "< OR = 5", "less than
# or equal to 5" and "> OR = 60" not at all, as Type them in did; the C-CDA, FHIR, CareSpace and MyChart importers
# no decimal comma; a PDF report no words; the FHIR export said "≤ 5" and "< OR = 5" as text alone
SPELLINGS = [
    ["3.5-5.1", "3.5", 3.5, 5.1, "", ""], ["3.5 - 5.1", "5.2", 3.5, 5.1, "", "H"], ["70–99", "70", 70, 99, "", ""],
    ["10 to 40", "9", 10, 40, "", "L"], ["5,0 - 10,0", "4.9", 5.0, 10.0, "", "L"], ["5..10", "10", 5, 10, "", ""],
    ["<5", "5", None, 5, "<", "H"], ["less than 5", "5", None, 5, "<", "H"], ["under 5", "4", None, 5, "<", ""],
    ["< 5 U/L", "5", None, 5, "<", "H"], ["<=5", "5", None, 5, "", ""], ["=<5", "5", None, 5, "", ""],
    ["≤ 5", "6", None, 5, "", "H"], ["< OR = 5", "5", None, 5, "", ""], ["less than or equal to 5", "5", None, 5, "", ""],
    ["up to 5", "6", None, 5, "", "H"], [">60", "60", 60, None, ">", "L"], ["greater than 60", "60", 60, None, ">", "L"],
    ["over 60", "61", 60, None, ">", ""], [">=60", "60", 60, None, "", ""], ["=>60", "90", 60, None, "", ""],
    ["=>60", "59", 60, None, "", "L"], ["≥ 60", "59", 60, None, "", "L"], ["> OR = 60", "60", 60, None, "", ""],
    ["greater than or equal to 60", "59", 60, None, "", "L"], ["Negative", "5", None, None, "", ""],
    # #870: in scientific notation, which common.range_text read no number of ("<1.0E4" was a high of 1)
    ["5.0E3-1.0E4", "12000", 5000.0, 10000.0, "", "H"], ["1.0E-3 - 5.0E-3", "0.004", 0.001, 0.005, "", ""],
    ["<1.0E4", "10000", None, 10000.0, "<", "H"],
]
SPELLED_AS = ("AST", "U/L")   # a lab every importer charts (CareSpace only those it knows)
# #859: no thousands follow a leading 0. COMMAS: a number as printed, as a value and in a range ("<0,500"), and what each
# reads as (None: unread, kept as printed and said so) where the file's decimal commas aren't known (a C-CDA document,
# FHIR, CareSpace, MyChart, a PDF report: a comma that can't group thousands is decimal), where its commas group
# thousands (a comma-delimited spreadsheet that writes decimal points, Type them in) and where they're decimal (a
# spreadsheet with semicolons, or with "0,500" or "4,5" among its values). "0,500" was 500 where commas group thousands.
# #870: and in scientific notation, read in every mode ("1.2E-3" was no number but in a spreadsheet's). Each read as a
# value by every importer that reads a printed one (C-CDA, CareSpace and PDF reports where the decimal mark isn't
# known), and the FHIR export of each import read back the same (the export sent "0,500" and "1,250" as text alone)
COMMAS = {"0,500": (0.5, None, 0.5), "0,05": (0.05, 0.05, 0.05), "1,250": (None, 1250, 1.25), "12,500": (None, 12500, 12.5),
          "1.2E-3": (0.0012, 0.0012, 0.0012), "5,0E3": (5000.0, 5000.0, 5000.0)}
UNKNOWN, IN_THOUSANDS, DECIMAL = 0, 1, 2   # COMMAS' modes
# #870: a value given as a bound, as reports print one, beside a range: BOUND_VALUES [value as printed, range as printed,
# its number, comparator, flag] ("=<5" against 10–40 is Low: every value it may be is under 10; "≤ 5" against "<5" may be
# inside it). Through a spreadsheet (comma-delimited, and with semicolons), Type them in, PDF reports (the ASCII ones), a
# C-CDA document (as text), a FHIR bundle (a Quantity's comparator), CareSpace and MyChart, and the FHIR export read
# back, of the spreadsheet's import and of rows alone, each gives that number, comparator and flag (bounded();
# tests/test_ranges.py test_2_bound_values). The export sent "=<5", "≤ 5" and "≥60" as text alone, and the FHIR importer
# kept every bound as text, so read back they had no number, range or flag
BOUND_VALUES = [["=<5", "10 - 40", 5, "<=", "L"], ["≤ 5", "<5", 5, "<=", ""], ["< 5", "5 - 10", 5, "<", "L"],
                ["<=5", "5 - 10", 5, "<=", ""], ["≥60", "<60", 60, ">=", "H"], ["=>60", ">=60", 60, ">=", ""],
                ["> 10", "5 - 10", 10, ">", "H"], [">= 60", "10 - 40", 60, ">=", "H"], ["<0.5", "<0.5", 0.5, "<", ""]]

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


# #845: what reads a flag reads it through ranges.js, none by its letter: Labs' list of labs (a ◆ was ▼), Settings'
# preview of the key-lab tiles (H and L as stored only) and the lab panel's counts (a ◆ inside its range counted outside
# it, a flagged result with no range as one with a range). Each case's results as judged (JUDGED, COPIES_SEEN, ...): the
# list's latest value with its mark (listed), and the panel's counts (panel)
MARK = {"H": "▲", "L": "▼", "A": "◆"}


def listed(flag, given, unit):
    """A lab's row in Labs' list: its latest value as given, its unit, its flag's mark before it: "◆ 20 U/L"."""
    return (MARK[flag] + " " if flag else "") + f"{given} {unit}"


def panel(judged):
    """The lab panel's "Inside own range", its row of those flagged by mark and "No range on the report" (by their keys,
    in, out and norange) for results judged so ([flag, low, high, ...] each): inside its range, one with a range and no ▲
    or ▼ (a ◆ is placed by its range where it can be: inside it); a flagged result with no range has none."""
    ranged = [lo is not None or hi is not None for _, lo, hi, *_ in judged]
    inside = sum(1 for (f, *_), r in zip(judged, ranged) if r and f not in ("H", "L"))
    out = [f"{MARK[k]} {n} {w}" for k, w in (("H", "above"), ("L", "below"), ("A", "flagged")) for n in [sum(1 for f, *_ in judged if f == k)] if n]
    want = {"in": f"{inside} of {sum(ranged)} ({int(inside / sum(ranged) * 100 + .5)}%)" if any(ranged) else "None has a range"}
    if any(ranged) or out:
        want["out"] = " · ".join(out) or "None"
    if not all(ranged):
        want["norange"] = f"{ranged.count(False)} result{'s' if ranged.count(False) > 1 else ''}"
    return want


def latest_of(cases, keys):
    """Of two imports' cases (COPIES, LATEST), each (lab, unit) of keys: its latest result's value as given; and each lab's
    tile, the (lab, unit) whose latest is the latest (the first, in import order, of two at one time), as the Overview
    and Settings' preview show it. -> ({(lab, unit): value}, {lab: (lab, unit)})"""
    at = {k: min((d, imp, t) for imp, rows in cases[k].items() for d, _, _, _, _, t, *_ in rows) for k in keys}
    tiles = {}
    for k in keys:
        if k[0] not in tiles or at[k][:2] < at[tiles[k[0]]][:2]:
            tiles[k[0]] = k
    return {k: v[2] for k, v in at.items()}, tiles


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


def printed_at(lab, i):
    """CASES[lab][i]'s range as its report prints it: as PRINTED spells it (#850), else printed_range()."""
    return PRINTED[lab][i] if lab in PRINTED else printed_range(*CASES[lab][i][2:4], edges(lab)[i])


def draw_days():
    """The days back with a lab draw, the oldest first."""
    return sorted({d for pts in CASES.values() for d, *_ in pts}, reverse=True)


def day_results(d):
    """A draw day's results: [(lab, its index in CASES[lab])], in CASES' order."""
    return [(n, i) for n, pts in CASES.items() for i, p in enumerate(pts) if p[0] == d]


def through_csv(today=None):
    """THROUGH_CSV's series as the CSV importer reads them (csv_labs.parse, #817), from a spreadsheet of their results
    as their reports print them: date, time, test, value as given, unit, range as printed (printed_at()) and flag as
    printed."""
    import os, tempfile
    from server.importers import csv_labs
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "flags.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Date,Time,Test,Value,Unit,Reference range,Flag\n" + "".join(
                f"{day(d, today)[:10]},{day(d, today)[11:16]},{n},{t},{UNITS[n]},{printed_at(n, i)},{flag}\n"
                for n in THROUGH_CSV for i, (d, v, lo, hi, flag, t) in enumerate(CASES[n])))
        return {s["name"]: s for s in csv_labs.parse({"flags.csv": path})["data"]["labs"]}


def pasted(lab, today=None):
    """lab's results as a spreadsheet's copy pasted into Type them in (#835): tab-separated, a header row, each flag as
    PASTE_FLAGS has it in an "Abnormal" column."""
    return "Date\tTime\tTest\tValue\tUnit\tReference range\tAbnormal\n" + "".join(
        f"{day(d, today)[:10]}\t{day(d, today)[11:16]}\t{lab}\t{t}\t{UNITS[lab]}\t{printed_range(lo, hi)}\t{f}\n"
        for (d, v, lo, hi, _, t), f in zip(CASES[lab], PASTE_FLAGS[lab]))


def ccda(rows):
    """A fictional C-CDA document of lab results, each [(test, unit, time, value as given, range as text)] an
    observation of its own, its reference range its text alone (no IVL, #827); a value given as a bound ("<5", "=<5") or
    with a comma ("0,500") as text, as a document gives one."""
    from html import escape
    from server.importers import common
    obs = []
    for n, u, t, v, r in rows:
        loinc = common.loinc_of(common.canonical_lab(n))
        code = f'code="{loinc}" codeSystem="2.16.840.1.113883.6.1" ' if loinc else ""
        value = (f'<value xsi:type="ST">{escape(v)}</value>' if common.comparator(v) or "," in v else f'<value xsi:type="PQ" value="{v}" unit="{escape(u)}"/>')
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


def spelled(today=None):
    """SPELLINGS' results, one a day, the oldest first: [(test, unit, time, value as given, range as printed)]."""
    n, u = SPELLED_AS
    return [(n, u, day(200 - i, today), v, r) for i, (r, v, *_) in enumerate(SPELLINGS)]


def bounded(today=None):
    """BOUND_VALUES' results, one a day, the oldest first: [(test, unit, time, value as given, range as printed)]."""
    n, u = SPELLED_AS
    return [(n, u, day(100 - i, today), v, r) for i, (v, r, *_) in enumerate(BOUND_VALUES)]


def commas_back(today=None):
    """#870's two spreadsheets, a day apart (tests/browser_check.py takes them through the FHIR export and back): one
    with semicolons and decimal commas ("4,5", "0,500", "=<5", "12,5"), one comma-delimited that writes decimal points
    ("1,250" in thousands, "≥60"); seven results, four outside their range. -> [(file name, its text)]"""
    a, b = day(2, today)[:10], day(1, today)[:10]
    return [("commas-eu.csv", "Date;Time;Test;Value;Unit;Reference range\n"
                              f"{a};08:00;Potassium;4,5;mmol/L;3,5 - 5,1\n{a};08:00;Creatinine;0,500;mg/dL;0,6 - 1,3\n"
                              f"{a};08:00;C-Reactive Protein;=<5;mg/L;<5\n{a};08:00;Hemoglobin;12,5;g/dL;13,5 - 17,5\n"),
            ("commas-us.csv", "Date,Time,Test,Value,Unit,Reference range\n"
                              f"{b},08:00,Platelet Count,\"1,250\",x10^3/uL,150 - 400\n{b},08:00,eGFR,≥60,mL/min/1.73m2,>=60\n"
                              f"{b},08:00,Sodium,147.0,mmol/L,135-145\n")]


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
    from server.importers import common
    num = common.value_number   # its numericValue
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
        rows = [[ROW_NAMES.get(n, n), CASES[n][j][5], UNITS[n], printed_at(n, j), stored[n][j]] for n, j in day_results(d)]
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

# ---- #847: two copies whose reports print different ranges, through the same uploads: in the edge ("<5" in the
# spreadsheet, "<=5" in the C-CDA; ">=60" and ">60") and in the limits (13.5–17.5 and 12–16). They were one result, the
# first's range and flag given to the other (the C-CDA's lab day said "<=5 ▲ High" for its CRP, and its eGFR, Low by its
# own report, showed nowhere). Now two each, everywhere, each with its own range and flag, at one time the flagged one
# after the other (Labs, a trend's Now, the Overview's list); an out-of-range row "1 of 2". CRP's and Hemoglobin's (by
# LOINC code) are results already here at the second upload, added with their own range (#846); eGFR's, with no code, a
# possible match from another source (UPLOADED_POSSIBLE)
UPLOADED.update({"CRP": ("mg/L", "5", "<5", "", "<=5"), "eGFR": ("mL/min/1.73m2", "60", ">=60", "", ">60"),
                 "Hemoglobin": ("g/dL", "13.0", "13.5-17.5", "", "12-16")})
UPLOADED_SEEN.update({("CRP", "mg/L"): [["", None, 5], ["H", None, 5, "<"]], ("eGFR", "mL/min/1.73m2"): [["", 60, None], ["L", 60, None, ">"]],
                      ("Hemoglobin", "g/dL"): [["", 12, 16], ["L", 13.5, 17.5]]})
UPLOADED_ROWS = {"csv": [("ALT", "A"), ("CRP", "H"), ("eGFR", ""), ("Glucose", "H"), ("Hemoglobin", "L"), ("Potassium", "H"), ("Sodium", "H")],
                 "ccda": [("ALT", "A"), ("CRP", ""), ("eGFR", "L"), ("Glucose", "H"), ("Hemoglobin", ""), ("Potassium", "H"), ("Sodium", "H")]}
UPLOADED_LABS = ["ALT", "CRP", "eGFR", "Glucose, Blood", "Hemoglobin", "Potassium, Blood", "Sodium, Blood"]
UPLOADED_TRENDS = {**{k: v for k, v in UPLOADED_TRENDS.items() if k[0] == "ALT"}, ("CRP", "mg/L"): [None, ["5", "High"], "< 5"],
                   ("eGFR", "mL/min/1.73m2"): [None, ["60", "Low"], "> 60"], **{k: v for k, v in UPLOADED_TRENDS.items() if k[0] == "Glucose, Blood"},
                   ("Hemoglobin", "g/dL"): [None, ["13.0", "Low"], "13.5–17.5"], **{k: v for k, v in UPLOADED_TRENDS.items() if k[0] > "H"}}
UPLOADED_OUT.update({("CRP", "mg/L"): ["5", "High", "< 5", 1, 2], ("eGFR", "mL/min/1.73m2"): ["60", "Low", "> 60", 1, 2],
                     ("Hemoglobin", "g/dL"): ["13.0", "Low", "13.5–17.5", 1, 2]})
UPLOADED_HERE, UPLOADED_POSSIBLE = {"copies": 6, "results": 0}, 1
# ---- #868: what each copy brings, by the second file of each order: its report's range (the C-CDA's for Sodium,
# Potassium and Glucose; either's own for CRP and Hemoglobin), its flag (the spreadsheet's "A" for ALT), or only its row
# in the file's own lab day (a file that printed nothing more of it). The review said "with its report's range and
# flag" of every copy, the spreadsheet's three that print neither too
UPLOADED_BRINGS = {"ccda": {"range": 5, "row": 1}, "csv": {"flag": 1, "range": 2, "row": 3}}
UPLOADED_COPIES_SAID = {"ccda": "6 lab results already here are added again from this file: 5 with its report's range, so neither report's is "
                                "lost, and 1 to go with its own lab day.",
                        "csv": "6 lab results already here are added again from this file: 2 with its report's range and 1 with its "
                               "report's flag, so neither report's is lost, and 3 to go with its own lab day."}


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


# ---------------------------------------------------------------- #868: a result already here, typed in again
# One rule for a result already here, Type them in's as an upload's: kept as a copy only with its own row, and said by
# what it brings. A profile's results typed in twice, a save each (TYPED: test, unit, value, range as typed, at
# TYPED_BACK days back): the second with Potassium's range (it had none), Sodium as before, and CRP's "<=5" for "<5".
# Each result's row is keyed by its test, time and value, so the second's were duplicates by key and left out, while
# Potassium's and CRP's results were kept as copies: its check line said "already in this profile: it won't be saved
# again", its save "Saved nothing new" with no Undo, and each typed lab day counted the copies with no row of their own
# (util.js labDraws). Now each copy comes with its row in its own lab day, its line says what it brings (TYPED_SAID:
# "range", or None for a duplicate, left out), and its save counts it (TYPED_SAVED, an Undo with it). TYPED_SEEN each
# result as Labs, the summary and a share link show it; TYPED_DAYS each typed import's lab day (its rows' tests as
# typed and the flags Labs shows); TYPED_TRENDS and TYPED_OUT the summary's since TYPED_SINCE days back
TYPED_BACK, TYPED_SINCE = 4, 10
TYPED = [[("C-Reactive Protein", "mg/L", "5", "<5"), ("Potassium", "mmol/L", "5.3", ""), ("Sodium", "mmol/L", "150", "135-145")],
         [("C-Reactive Protein", "mg/L", "5", "<=5"), ("Potassium", "mmol/L", "5.3", "3.5-5.1"), ("Sodium", "mmol/L", "150", "135-145")]]
TYPED_SAID = ["range", "range", None]
COPY_SAID = {"range": "already in this profile: added again with its range, so neither is lost",
             "flag": "already in this profile: added again with its flag, so neither is lost",
             None: "already in this profile: it won't be saved again"}
TYPED_SAVED = "Saved 2 results. 1 was in this profile already and left out."
TYPED_SEEN = {("C-Reactive Protein", "mg/L"): [["", None, 5], ["H", None, 5, "<"]], ("Potassium, Blood", "mmol/L"): [["H", 3.5, 5.1]],
              ("Sodium, Blood", "mmol/L"): [["H", 135, 145]]}
TYPED_DAYS = {(1, TYPED_BACK): [("C-Reactive Protein", "H"), ("Potassium", "H"), ("Sodium", "H")],
              (2, TYPED_BACK): [("C-Reactive Protein", ""), ("Potassium", "H")]}
TYPED_LABS = ["C-Reactive Protein", "Potassium, Blood", "Sodium, Blood"]
TYPED_TRENDS = {("C-Reactive Protein", "mg/L"): [None, ["5", "High"], "< 5"], ("Potassium, Blood", "mmol/L"): [None, ["5.3", "High"], "3.5–5.1"],
                ("Sodium, Blood", "mmol/L"): [None, ["150", "High"], "135–145"]}
TYPED_OUT = {("C-Reactive Protein", "mg/L"): ["5", "High", "< 5", 1, 2], ("Potassium, Blood", "mmol/L"): ["5.3", "High", "3.5–5.1", 1, 1],
             ("Sodium, Blood", "mmol/L"): ["150", "High", "135–145", 1, 1]}


# ---------------------------------------------------------------- #869: the same record sent again by its source
# A FHIR file's results (RESENT, at RESENT_BACK days back), sent again with what its source amended (resent(1)): the
# report's Hemoglobin with its range corrected and its Low taken back (13.5–17.5 "L", then 12.0–15.5 "N"), its WBC's
# range given (none, then 4.5–11.0, so High), a Potassium's range corrected (3.5–5.3, then 3.5–5.1: High) and an
# ALT's flag changed ("H" with no range, then "A": Flagged). Each Observation the same, by its id in the report or by its
# day (its labitem's key), its value and time the same. The second upload added a copy of each beside the first, read
# as one result (#825) or, its range another, as two (#847), its lab day's rows (the first's, by key) reading the old
# point's flag beside the new range. Now each is updated in place, range and flag, the drawer's row with it: one result
# each, as the second file says. RESENT: lab (code text) -> (LOINC, unit, value, [(range text, low, high, interpretation)
# first, then]); RESENT_HERE the second's review; RESENT_SEEN each result as every view shows it; RESENT_DAYS the lab
# day's rows (the test as printed, its flag) and RESENT_RANGES each row's Ref range as printed now; RESENT_TRENDS and
# RESENT_OUT the summary's since RESENT_SINCE days back
RESENT_BACK, RESENT_SINCE = 5, 10
RESENT = {"Hemoglobin": ("718-7", "g/dL", 12.1, [("13.5-17.5", 13.5, 17.5, "L"), ("12.0-15.5", 12.0, 15.5, "N")], "report"),
          "WBC": ("6690-2", "10*3/uL", 12.5, [(None, None, None, None), ("4.5-11.0", 4.5, 11.0, None)], "report"),
          "Potassium": ("2823-3", "mmol/L", 5.2, [("3.5-5.3", 3.5, 5.3, None), ("3.5-5.1", 3.5, 5.1, None)], None),
          "ALT": ("1742-6", "U/L", 60, [(None, None, None, "H"), (None, None, None, "A")], None)}
RESENT_HERE = {"updates": 4, "copies": 0, "results": 0}
RESENT_SAID = "4 lab results already here from the same source are updated with the range and flag this file gives now, not added again."
RESENT_SEEN = {("ALT", "U/L"): [["A", None, None]], ("Hemoglobin", "g/dL"): [["", 12, 15.5]], ("WBC", "10*3/uL"): [["H", 4.5, 11]],
               ("Potassium, Blood", "mmol/L"): [["H", 3.5, 5.1]]}
RESENT_DAYS = {(1, RESENT_BACK): [("Hemoglobin", ""), ("WBC", "H"), ("ALT", "A"), ("Potassium", "H")]}
RESENT_RANGES = ["12.0-15.5", "4.5-11.0", "", "3.5-5.1"]
RESENT_LABS = ["ALT", "Hemoglobin", "Potassium, Blood", "WBC"]
RESENT_TRENDS = {("ALT", "U/L"): [None, ["60", "Flagged"], ""], ("Hemoglobin", "g/dL"): [None, ["12.1", None], "12–15.5"],
                 ("Potassium, Blood", "mmol/L"): [None, ["5.2", "High"], "3.5–5.1"], ("WBC", "10*3/uL"): [None, ["12.5", "High"], "4.5–11"]}
RESENT_OUT = {("ALT", "U/L"): ["60", "Flagged", "", 1, 1], ("WBC", "10*3/uL"): ["12.5", "High", "4.5–11", 1, 1],
              ("Potassium, Blood", "mmol/L"): ["5.2", "High", "3.5–5.1", 1, 1]}


def resent(k, today=None):
    """RESENT as its source sends it the first time (k 0) or again, amended (k 1): (a file's name, a FHIR Bundle's text),
    the report's results under a DiagnosticReport, the rest on their own (keyed by their day)."""
    import json
    t = day(RESENT_BACK, today)
    obs = []
    for name, (code, unit, v, sent, where) in RESENT.items():
        text, lo, hi, interp = sent[k]
        r = {"resourceType": "Observation", "id": "obs-" + code, "status": "final", "category": [{"coding": [{"code": "laboratory"}]}],
             "code": {"coding": [{"system": "http://loinc.org", "code": code}], "text": name}, "effectiveDateTime": t,
             "valueQuantity": {"value": v, "unit": unit}}
        if text:
            r["referenceRange"] = [{"low": {"value": lo, "unit": unit}, "high": {"value": hi, "unit": unit}, "text": text}]
        if interp:
            r["interpretation"] = [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": interp}]}]
        obs.append(r)
    report = {"resourceType": "DiagnosticReport", "id": "dr-cbc", "status": "final", "category": [{"coding": [{"code": "LAB"}]}],
              "code": {"text": "CBC"}, "effectiveDateTime": t,
              "result": [{"reference": "Observation/" + o["id"]} for o, (_, (*_, where)) in zip(obs, RESENT.items()) if where]}
    return f"labs-{k + 1}.json", json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in obs + [report]]})


def typed(k, today=None):
    """TYPED's k-th save as Type them in's rows ({date, time, test, value, unit, range}), and as a spreadsheet's copy to
    paste into it (a header, then a row each, tab-separated)."""
    t = day(TYPED_BACK, today)
    rows = [{"date": t[:10], "time": t[11:16], "test": n, "value": v, "unit": u, "range": r} for n, u, v, r in TYPED[k]]
    return rows, "Date\tTime\tTest\tValue\tUnit\tReference range\n" + "".join("\t".join(r[f] for f in ("date", "time", "test", "value", "unit", "range")) + "\n" for r in rows)


# ---------------------------------------------------------------- #848: two results at one time, each said
# A profile of two imports (copies(1, cases=AT_ONCE), then copies(2, ...)): Potassium's draws, and at one time
# (AT_ONCE_DAY days back) a result from each import, not copies (two values, each its own range): 5.0 inside 3.5–5.1,
# and 5.6 above 3.5–5.3, its report's H; and Sodium's, to compare it with. Labs' chart, the lab panel's strip and Compare
# step from the keyboard through that time once, on to the next (→ stayed there for good), and say both, in the
# readout, the tooltip and what's announced (what read the point nearest read the first, so the ▲ High one, drawn last
# over it, was never said). AT_ONCE_DAYS: the draws, days back. AT_ONCE_SAID: each said as Labs' readout and the strip's
# tooltip tell them apart, the flagged one first ("each"); Labs' readout ("labs"), Compare's slot readout
# ("compare_read") and its tooltip's line ("compare")
AT_ONCE = {
    ("Potassium", "mmol/L"): {1: [(60, 4.1, 3.5, 5.1, "", "4.1"), (30, 5.0, 3.5, 5.1, "", "5.0"), (10, 4.4, 3.5, 5.1, "", "4.4")],
                              2: [(30, 5.6, 3.5, 5.3, "H", "5.6")]},
    ("Sodium", "mmol/L"): {1: [(60, 140, 135, 145, "", "140"), (30, 141, 135, 145, "", "141"), (10, 139, 135, 145, "", "139")]},
}
AT_ONCE_DAYS, AT_ONCE_DAY = [60, 30, 10], 30
AT_ONCE_SAID = {"each": ["5.6 mmol/L ▲ High (3.5–5.3)", "5.0 mmol/L (3.5–5.1)"],
                "labs": "2 results: 5.6 mmol/L ▲ High (3.5–5.3); 5.0 mmol/L (3.5–5.1) · ",
                "compare_read": "2 results: 5.6 mmol/L · 117%; 5.0 mmol/L · 94% · ",
                "compare": "Potassium: 2 results: 5.6 mmol/L, 117% of its range (3.5–5.3) · ▲ Above range; 5.0 mmol/L, 94% of its range (3.5–5.1)"}
