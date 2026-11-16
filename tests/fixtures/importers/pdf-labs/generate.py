#!/usr/bin/env python3
"""Writes the PDF lab report fixtures: a Quest-like and a Labcorp-like report, an unrecognised PDF and a scanned
(image-only) one. Every patient, provider, number and value is made up; the layouts only mimic the labs' reports.

  python3 -I tests/fixtures/importers/pdf-labs/generate.py      # rewrites the fixtures next to this file

Standard library only, and deterministic: the same script writes the same bytes (tests/test_importer_pdf_labs.py
checks the committed fixtures match). tests/test_importer_pdf_labs.py also builds variants with quest() and write().
"""
import os, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------------ a small PDF writer: positioned text, or one image
def _esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write(path, pages, image=None):
    """pages: [[(x, y, text, size, bold), ...] per page]; image: (width, height, gray bytes) to draw as the whole
    single page instead, with no text at all (a scan)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None]
    kids = []
    fonts = "<< /F1 {r} 0 R /F2 {b} 0 R >>"
    if image:
        w, h, gray = image
        body = b"q 612 0 0 792 0 0 cm /Im1 Do Q"
        objs.append(None)   # page, filled below
        kids.append(len(objs))
        objs.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(zlib.compress(body, 9)) + zlib.compress(body, 9) + b"\nendstream")
        data = zlib.compress(gray, 9)
        objs.append(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 "
                    b"/Filter /FlateDecode /Length %d >>\nstream\n" % (w, h, len(data)) + data + b"\nendstream")
        objs[kids[0] - 1] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {kids[0] + 1} 0 R "
                             f"/Resources << /XObject << /Im1 {kids[0] + 2} 0 R >> >> >>")
    else:
        first_font = 3 + 2 * len(pages)
        for i, cells in enumerate(pages):
            ops = ["BT"]
            for x, y, text, size, bold in cells:
                ops.append(f"/F{2 if bold else 1} {size} Tf 1 0 0 1 {x} {y} Tm ({_esc(text)}) Tj")
            ops.append("ET")
            body = zlib.compress("\n".join(ops).encode("latin-1"), 9)
            kids.append(3 + 2 * i)
            objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
                        f"/Resources << /Font {fonts.format(r=first_font, b=first_font + 1)} >> >>")
            objs.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(body) + body + b"\nendstream")
        objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    info = len(objs) + 1
    objs.append("<< /Title (Fictional test fixture) /Producer (Sanguity tests) >>")
    out, offs = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n", []
    for i, o in enumerate(objs):
        offs.append(len(out))
        out += b"%d 0 obj\n" % (i + 1) + (o if isinstance(o, bytes) else o.encode("latin-1")) + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, info, x)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(out)


def _t(x, y, text, size=9, bold=False):
    return (x, y, text, size, bold)


# ------------------------------------------------------------------ a Quest-like report
QUEST_ROWS = [   # (panel heading or None, name, value, flag, range, unit)
    ("COMPREHENSIVE METABOLIC PANEL", None),
    ("GLUCOSE", "88", "", "65-99", "mg/dL"),
    ("UREA NITROGEN (BUN)", "14", "", "7-25", "mg/dL"),
    ("CREATININE", "1.32", "H", "0.50-1.05", "mg/dL"),
    ("EGFR", "47", "L", "> OR = 60", "mL/min/1.73m2"),
    ("SODIUM", "140", "", "135-146", "mmol/L"),
    ("POTASSIUM", "4.3", "", "3.5-5.3", "mmol/L"),
    ("CHLORIDE", "103", "", "98-110", "mmol/L"),
    ("CARBON DIOXIDE", "26", "", "20-32", "mmol/L"),
    ("CALCIUM", "9.4", "", "8.6-10.4", "mg/dL"),
    ("PROTEIN, TOTAL", "7.0", "", "6.1-8.1", "g/dL"),
    ("ALBUMIN", "4.3", "", "3.6-5.1", "g/dL"),
    ("ALKALINE PHOSPHATASE", "71", "", "37-153", "U/L"),
    ("AST", "22", "", "10-35", "U/L"),
    ("ALT", "19", "", "6-29", "U/L"),
    ("CBC (INCLUDES DIFF/PLT)", None),
    ("WHITE BLOOD CELL COUNT", "6.1", "", "3.8-10.8", "Thousand/uL"),
    ("RED BLOOD CELL COUNT", "4.02", "", "3.80-5.10", "Million/uL"),
    ("HEMOGLOBIN", "11.4", "L", "11.7-15.5", "g/dL"),
    ("HEMATOCRIT", "35.2", "", "35.0-45.0", "%"),
    ("MCV", "87.6", "", "80.0-100.0", "fL"),
    ("PLATELET COUNT", "245", "", "140-400", "Thousand/uL"),
    "PAGE",
    ("LIPID PANEL", None),
    ("CHOLESTEROL, TOTAL", "212", "H", "<200", "mg/dL"),
    ("HDL CHOLESTEROL", "58", "", "> OR = 50", "mg/dL"),
    ("TRIGLYCERIDES", "96", "", "<150", "mg/dL"),
    ("LDL-CHOLESTEROL", "134", "H", "<100", "mg/dL (calc)"),
    ("HEMOGLOBIN A1c", "5.4", "", "<5.7", "% of total Hgb"),
    ("HEPATITIS C ANTIBODY", "NON-REACTIVE", "", "NON-REACTIVE", ""),
    ("TSH", "<0.01", "L", "0.40-4.50", "mIU/L"),
]
QUEST_LONE = {"HEMOGLOBIN A1c", "HEPATITIS C ANTIBODY", "TSH"}   # tests ordered on their own: at the panels' level
QUEST_PATIENT = "TESTPERSON, MARGOT"


def quest(rows=QUEST_ROWS, collected="03/14/2026 / 08:32 EDT"):
    """The pages of a fictional Quest-like report. rows: QUEST_ROWS' shape; a str row is printed as it is, in the
    name column (for tests of lines the layout doesn't read)."""
    def head(y):
        return [_t(40, y, "Test Name", 9, True), _t(250, y, "In Range", 9, True), _t(330, y, "Out Of Range", 9, True),
                _t(420, y, "Reference Range", 9, True), _t(566, y, "Lab", 9, True)]

    banner = [_t(40, 750, "Quest Diagnostics", 16, True), _t(430, 754, "Patient Report", 11, True),
              _t(40, 732, "FICTIONAL TEST REPORT - made-up patient and values", 8)]
    page1 = banner + [
        _t(40, 708, "Patient Information", 9, True), _t(230, 708, "Specimen Information", 9, True),
        _t(420, 708, "Client Information", 9, True),
        _t(40, 694, QUEST_PATIENT), _t(230, 694, "Specimen: ZX918273A"), _t(420, 694, "Client #: 00000000"),
        _t(40, 682, "DOB: 04/12/1971  AGE: 55"), _t(230, 682, "Requisition: 0007712"),
        _t(420, 682, "RIVERBEND FAMILY MEDICINE"),
        _t(40, 670, "Gender: F"), _t(230, 670, f"Collected: {collected}"), _t(420, 670, "100 EXAMPLE WAY"),
        _t(40, 658, "Patient ID: 0000000"), _t(230, 658, "Received: 03/14/2026 / 12:10 EDT"),
        _t(230, 646, "Reported: 03/15/2026 / 06:45 EDT"),
        _t(40, 630, "Ordering Physician: LIN, AVERY"),
    ]
    pages, cells, y = [], page1 + head(604), 588
    total = 1 + sum(1 for r in rows if r == "PAGE")
    for r in rows:
        if r == "PAGE":
            cells.append(_t(270, 40, f"Page {len(pages) + 1} of {total}", 8))
            pages.append(cells)
            cells = banner + [_t(40, 708, f"Patient: {QUEST_PATIENT}    DOB: 04/12/1971    Specimen: ZX918273A", 9)] + head(684)
            y = 668
            continue
        if isinstance(r, str):
            cells.append(_t(50, y, r))
        elif r[1] is None:
            cells.append(_t(40, y, r[0], 9, True))
        else:
            name, value, flag, rng, unit = r
            cells.append(_t(40 if name in QUEST_LONE else 50, y, name))
            cells.append(_t(330 if flag else 250, y, f"{value} {flag}" if flag else value))
            ru = " ".join(x for x in (rng, unit) if x)
            if ru:
                cells.append(_t(420, y, ru))
            cells.append(_t(566, y, "AMD"))
        y -= 14
    cells += [_t(40, y - 16, "Performing Site:", 9, True),
              _t(40, y - 30, "AMD  EXAMPLE REGIONAL LABORATORY, 1 TEST PLAZA, NOWHERE, ST 00000")]
    cells.append(_t(270, 40, f"Page {len(pages) + 1} of {total}", 8))
    pages.append(cells)
    return pages


# ------------------------------------------------------------------ a Labcorp-like report
LABCORP_ROWS = [   # (panel heading, None) or (name, result, flag, units, reference)
    ("CBC With Differential/Platelet", None),
    ("WBC", "7.2", "", "x10E3/uL", "3.4-10.8"),
    ("RBC", "4.09", "Low", "x10E6/uL", "4.14-5.80"),
    ("Hemoglobin", "13.9", "", "g/dL", "13.0-17.7"),
    ("Hematocrit", "41.5", "", "%", "37.5-51.0"),
    ("MCV", "90", "", "fL", "79-97"),
    ("Platelets", "212", "", "x10E3/uL", "150-450"),
    ("Neutrophils", "58", "", "%", "Not Estab."),
    ("Neutrophils (Absolute)", "4.2", "", "x10E3/uL", "1.4-7.0"),
    ("Comp. Metabolic Panel (14)", None),
    ("Glucose", "104", "High", "mg/dL", "70-99"),
    ("BUN", "16", "", "mg/dL", "6-24"),
    ("Creatinine", "1.01", "", "mg/dL", "0.76-1.27"),
    ("eGFR", "92", "", "mL/min/1.73", ">59"),
    ("Sodium", "139", "", "mmol/L", "134-144"),
    ("Potassium", "4.6", "", "mmol/L", "3.5-5.2"),
    "PAGE",
    ("Lipid Panel", None),
    ("Cholesterol, Total", "188", "", "mg/dL", "100-199"),
    ("Triglycerides", "162", "High", "mg/dL", "0-149"),
    ("HDL Cholesterol", "44", "", "mg/dL", ">39"),
    ("LDL Chol Calc (NIH)", "114", "High", "mg/dL", "0-99"),
    ("Hemoglobin A1c", None),
    ("Hemoglobin A1c", "5.9", "High", "%", "4.8-5.6"),
    ("Hepatitis C Virus Ab", None),
    ("Hep C Virus Ab", "Non Reactive", "", "", "Non Reactive"),
    ("Vitamin D, 25-Hydroxy", None),
    ("Vitamin D, 25-Hydroxy", "18.2", "Low", "ng/mL", "30.0-100.0"),
]
LABCORP_PATIENT = "Testperson, Jordan R"


def labcorp(rows=LABCORP_ROWS):
    def head(y):
        return [_t(40, y, "Tests", 9, True), _t(230, y, "Result", 9, True), _t(310, y, "Flag", 9, True),
                _t(370, y, "Units", 9, True), _t(450, y, "Reference Interval", 9, True), _t(566, y, "Lab", 9, True)]

    banner = [_t(40, 750, "labcorp", 18, True), _t(430, 754, "Patient Report", 11, True),
              _t(40, 732, "FICTIONAL TEST REPORT - made-up patient and values", 8)]
    page1 = banner + [
        _t(40, 708, f"Patient Name: {LABCORP_PATIENT}"), _t(330, 708, "Patient ID: 00000000"),
        _t(40, 696, "DOB: 09/02/1980    Age: 46    Sex: Male"), _t(330, 696, "Specimen ID: 126-418-2290-0"),
        _t(40, 684, "Ordering Physician: P SALAZAR"), _t(330, 684, "Account Number: 00000000"),
        _t(40, 672, "Date Collected: 05/02/2026    Time Collected: 07:45 AM CDT"),
        _t(40, 660, "Date Received: 05/02/2026    Date Reported: 05/04/2026"),
    ]
    pages, cells, y = [], page1 + head(632), 616
    total = 1 + sum(1 for r in rows if r == "PAGE")
    for r in rows:
        if r == "PAGE":
            cells.append(_t(270, 40, f"Page {len(pages) + 1} of {total}", 8))
            pages.append(cells)
            cells = banner + [_t(40, 708, f"Patient Name: {LABCORP_PATIENT}    DOB: 09/02/1980    Specimen ID: 126-418-2290-0", 9)] + head(684)
            y = 668
            continue
        if isinstance(r, str):
            cells.append(_t(48, y, r))
        elif r[1] is None:
            cells.append(_t(40, y, r[0], 9, True))
        else:
            name, value, flag, unit, rng = r
            cells.append(_t(48, y, name))
            cells.append(_t(230, y, value))
            for x, s in ((310, flag), (370, unit), (450, rng)):
                if s:
                    cells.append(_t(x, y, s))
            cells.append(_t(566, y, "01"))
        y -= 14
    cells += [_t(40, y - 16, "Performing Labs", 9, True),
              _t(40, y - 30, "01  Example Reference Laboratory, 2 Sample Road, Nowhere, ST 00000")]
    cells.append(_t(270, 40, f"Page {len(pages) + 1} of {total}", 8))
    pages.append(cells)
    return pages


# ------------------------------------------------------------------ PDFs that aren't lab reports Sanguity reads
def unrecognised():
    return [[_t(40, 740, "Riverbend Family Medicine", 14, True), _t(40, 716, "Visit summary (fictional)"),
             _t(40, 700, "Patient: Testperson, Margot"), _t(40, 684, "Reason for visit: annual check-up"),
             _t(40, 668, "Plan: repeat blood work in six months.")]]


def scanned():
    w, h = 64, 82   # a gray "photo" of a page: noise, no text layer
    seed, px = 12345, bytearray()
    for _ in range(w * h):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        px.append(200 + (seed >> 16) % 56)
    return w, h, bytes(px)


def main(out=HERE):
    write(os.path.join(out, "quest", "quest-report.pdf"), quest())
    write(os.path.join(out, "labcorp", "labcorp-report.pdf"), labcorp())
    write(os.path.join(out, "unrecognised.pdf"), unrecognised())
    write(os.path.join(out, "scanned.pdf"), [], image=scanned())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else HERE)
