#!/usr/bin/env python3
"""Generate a fictional demo dataset for Sanguity.

Everything here is made up: the patient, providers, insurer, values and documents. The dataset tells a
two-year story that exercises every tab: routine labs, a diagnosis, six treatment cycles with the usual
blood-count dips, one hospital stay, recovery with physical therapy, insurance claims, payments, and
watch + smart-scale data.

Usage:  python3 -I demo/generate.py [OUT_DIR] [--end YYYY-MM-DD] [--big]
        OUT_DIR defaults to www/ and receives data.json, settings.json, watch/daily.json and
        files/demo-report.pdf (all git-ignored). --watch DIR writes daily.json there instead (the Docker
        setup points it at the receiver's public folder). The same --end date always produces the same data.
        --big adds eight more years before the story (#201): about 5,000 lab results in 47 tests, 2,000 claims,
        1,000 small documents (files/big/) and ten years of daily watch data, for the performance budgets
        (docs/performance.md, tests/test_performance.py). Still fictional, still the same on every run.
"""
import json, math, os, random, sys
from datetime import date, datetime, timedelta

argv = sys.argv[1:]
WATCH_DIR, BIG = None, "--big" in argv
argv = [a for a in argv if a != "--big"]
if "--watch" in argv:
    i = argv.index("--watch"); WATCH_DIR = argv[i + 1]; del argv[i:i + 2]
sys.argv = sys.argv[:1] + argv
args = [a for a in sys.argv[1:] if not a.startswith("--end")]
END = date.today()
for i, a in enumerate(sys.argv):
    if a.startswith("--end"):
        END = date.fromisoformat(a.split("=", 1)[1] if "=" in a else sys.argv[i + 1])
        if "=" not in a:
            args = [x for x in args if x != sys.argv[i + 1]]
OUT = args[0] if args else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "www")

R = random.Random(END.toordinal())          # deterministic for a given end date
START = END - timedelta(days=730)
DX = START + timedelta(days=150)            # diagnosis
TX = DX + timedelta(days=21)                # first treatment cycle
CYCLES, CYCLE_DAYS = 6, 21
TX_END = TX + timedelta(days=CYCLES * CYCLE_DAYS)
STAY_CYCLE = 3                              # febrile-neutropenia admission during this cycle
FIRST = END - timedelta(days=3652) if BIG else START   # --big: ten years of records (#201)

CANCER = "Riverbend Cancer Center"
CLINIC = "Lakeside Family Medicine"
HOSPITAL = "Northfield General Hospital"
PT = "Stride Physical Therapy"
INSURER = "Example Health Plan"
ONC, PCP, THER = "Dr. Avery Lin", "Dr. Sam Okafor", "Jordan Reyes, PT"


def at(d, h=9, m=0):
    return datetime(d.year, d.month, d.day, h, m).isoformat()


events, details, costs, payments = [], {}, [], []
eid = 0


def add(t, k, title, sub="", detail=None, end=None, **extra):
    global eid
    eid += 1
    e = {"id": eid, "t": t, "k": k, "title": title, "sub": sub}
    if end:
        e["end"] = end
    e.update({a: b for a, b in extra.items() if b not in (None, "", [], 0)})
    if detail:
        details[eid] = detail
    events.append(e)
    return eid


# ------------------------------------------------------------------ claims
claim_no = 7_000_000
oop_used = {}


def claim(d, prov, title, billed, cat, kind="medical", drug=None, denied=False):
    """Insurer pays ~45% of billed (network discount); you owe 20% coinsurance up to a $4,000 yearly max."""
    global claim_no
    claim_no += R.randint(3, 40)
    allowed = round(billed * R.uniform(0.42, 0.58), 2)
    yr = d.year
    if denied:
        paid = owe = 0.0
        status = "PROVIDER_DENIED"
    else:
        share = 0.0 if kind == "pharmacy" else round(allowed * 0.2, 2)
        share = min(share, max(0.0, 4000 - oop_used.get(yr, 0)))
        if kind == "pharmacy":
            share = 15.0
        oop_used[yr] = oop_used.get(yr, 0) + share
        paid, owe, status = round(allowed - share, 2), round(share, 2), "PROCESSED"
    i = add(at(d, 12), "claim", title, f"{INSURER} · {prov}",
            {"type": "claim", "insurer": INSURER, "kind": kind, "provider": prov, "drug": drug, "claimId": f"EX{claim_no}",
             "status": status, "network": "IN_NETWORK", "billed": billed, "paid": paid, "owe": owe,
             "lines": [{"date": d.isoformat(), "desc": title, "code": "", "billed": billed,
                        **({"denied": ["Missing referral on file"]} if denied else {})}]})
    costs.append({"id": i, "d": d.isoformat(), "ins": INSURER, "billed": billed, "paid": paid, "owe": owe,
                  "status": status, "kind": kind, "cat": cat, "prov": prov, "title": title})
    return owe


owed_bills = []   # (date, payee, amount) to be paid out of pocket later

# ------------------------------------------------------------------ labs
LABS = {   # name: (unit, panel, lo, hi, baseline, decimals)
    "Hemoglobin": ("g/dL", "CBC WITH DIFFERENTIAL", 13.5, 17.5, 15.1, 1),
    "Platelet Count": ("K/uL", "CBC WITH DIFFERENTIAL", 150, 400, 265, 0),
    "WBC": ("K/uL", "CBC WITH DIFFERENTIAL", 4.0, 11.0, 6.8, 1),
    "Automated Abs Neutrophil Cnt": ("K/uL", "CBC WITH DIFFERENTIAL", 1.8, 7.7, 4.1, 2),
    "Lymphocytes Absolute": ("K/uL", "CBC WITH DIFFERENTIAL", 1.0, 4.8, 1.9, 2),
    "Creatinine": ("mg/dL", "COMPREHENSIVE METABOLIC PANEL", 0.7, 1.3, 0.92, 2),
    "ALT": ("U/L", "COMPREHENSIVE METABOLIC PANEL", 7, 56, 24, 0),
    "AST": ("U/L", "COMPREHENSIVE METABOLIC PANEL", 10, 40, 22, 0),
    "Total Bilirubin": ("mg/dL", "COMPREHENSIVE METABOLIC PANEL", 0.1, 1.2, 0.6, 1),
    "Potassium, Blood": ("mmol/L", "COMPREHENSIVE METABOLIC PANEL", 3.5, 5.1, 4.2, 1),
    "Sodium, Blood": ("mmol/L", "COMPREHENSIVE METABOLIC PANEL", 136, 145, 140, 0),
    "Magnesium": ("mg/dL", "MAGNESIUM", 1.7, 2.2, 2.0, 1),
}
CORE = list(LABS)
CBC = [n for n, v in LABS.items() if v[1].startswith("CBC")]
CMP = [n for n, v in LABS.items() if v[1].startswith("COMP")]
series = {n: [] for n in LABS}


def lab_value(name, d):
    unit, panel, lo, hi, base, dec = LABS[name]
    v = base
    if d >= DX - timedelta(days=60):                      # a slow drift that prompted the work-up
        v *= {"Hemoglobin": 0.93, "Lymphocytes Absolute": 0.8}.get(name, 1)
    if TX <= d < TX_END + timedelta(days=28):
        k, day = divmod((d - TX).days, CYCLE_DAYS)
        nadir = math.exp(-((day - 10) / 3.5) ** 2) if k < CYCLES else 0
        depth = {"Automated Abs Neutrophil Cnt": 0.88, "WBC": 0.72, "Platelet Count": 0.55, "Lymphocytes Absolute": 0.6,
                 "Hemoglobin": 0.08}.get(name, 0)
        v *= 1 - depth * nadir
        if name == "Hemoglobin":
            v -= 0.32 * min(k, CYCLES)                    # cumulative anemia across cycles
        if name in ("ALT", "AST") and day in (6, 7, 8) and k in (1, 2, 4):
            v *= 2.7
        if name == "Magnesium":
            v *= 0.88
    elif d >= TX_END + timedelta(days=28):               # recovery over ~6 months
        frac = min(1, (d - TX_END).days / 180)
        if name == "Hemoglobin":
            v -= 1.9 * (1 - frac)
        if name in ("Lymphocytes Absolute", "Platelet Count"):
            v *= 0.75 + 0.25 * frac
    v *= R.gauss(1, 0.01 if name in ("Sodium, Blood", "Potassium, Blood") else 0.035)   # electrolytes are tightly regulated
    return round(v, dec) if dec else round(v)


def draw(d, names, h=8, inpatient=False):
    t = at(d, h, R.randint(0, 50))
    ids, abn, orders = [], 0, []
    for order in dict.fromkeys(v[1] for v in LABS.values()):   # one result per panel, in LABS' order
        members = [n for n in names if LABS[n][1] == order]
        if not members:
            continue
        rows, a = [], 0
        for n in members:
            unit, _, lo, hi, _, _ = LABS[n]
            v = lab_value(n, d)
            flag = "L" if v < lo else "H" if v > hi else ""
            a += bool(flag)
            rows.append([n, str(v), unit, f"{lo} - {hi}", flag])
            series[n].append([t, v, lo, hi, flag, str(v)])
        where = HOSPITAL if inpatient else CANCER if d >= DX - timedelta(days=14) else CLINIC
        prov = ONC if where != CLINIC else PCP
        det = {"type": "result", "order": order, "collected": t[:16].replace("T", " "), "resulted": t[:10],
               "provider": prov, "status": "Final", "specimen": "Blood", "lab": f"{where} Laboratory", "dx": [], "rows": rows,
               "report": [], "comments": []}
        ids.append(add(t, "labitem", order, f"{prov} · Final · {where}", det, abn=a, hidden=1))
        abn += a
        orders.append(order)
    nv = sum(len(details[i]["rows"]) for i in ids)   # the title counts the values, as the drawer's Results fact does (#458)
    add(t, "lab", f"{nv} lab result{'s' if nv != 1 else ''}", ", ".join(orders), {"type": "labday", "ids": ids}, abn=abn)
    if not inpatient:
        owed_bills.append((d, where, claim(d, where, "Laboratory tests", round(R.uniform(140, 420), 2), "Lab & pathology")))


draw_days = set()
d = START + timedelta(days=12)
while d < DX - timedelta(days=20):                      # routine, every ~2 months
    draw_days.add((d, "full"))
    d += timedelta(days=R.randint(50, 70))
for off in (-20, -6, 0):
    draw_days.add((DX + timedelta(days=off), "full"))
stay_start = TX + timedelta(days=(STAY_CYCLE - 1) * CYCLE_DAYS + 9)
stay_end = stay_start + timedelta(days=4)
for k in range(CYCLES):
    c0 = TX + timedelta(days=k * CYCLE_DAYS)
    for day in (0, 7, 10, 14):
        dd = c0 + timedelta(days=day)
        if not (stay_start <= dd <= stay_end):
            draw_days.add((dd, "full" if day in (0, 14) else "liver" if day == 7 else "cbc"))
d = TX_END + timedelta(days=14)
while d < END - timedelta(days=3):                      # follow-up: monthly, then every 2 months
    draw_days.add((d, "full"))
    d += timedelta(days=30 if d < TX_END + timedelta(days=200) else 60)
for dd, kind in sorted(draw_days):
    names = CORE if kind == "full" else CBC + ["ALT", "AST", "Total Bilirubin"] if kind == "liver" else CBC
    if kind == "full" and R.random() < 0.5:
        names = [n for n in names if n != "Magnesium"]
    draw(dd, names)
for i in range((stay_end - stay_start).days + 1):     # daily inpatient labs
    draw(stay_start + timedelta(days=i), CBC + ["Creatinine", "Potassium, Blood"], h=5, inpatient=True)

# ------------------------------------------------------------------ hospital stay
add(at(stay_start, 21, 40), "stay", f"Hospital stay · {HOSPITAL}", "Fever during low blood counts · admitted via emergency department",
    {"type": "stay", "discharge": True, "where": f"{HOSPITAL}, oncology unit", "how": "Admission record"},
    end=at(stay_end, 14, 10))
owed_bills.append((stay_end, HOSPITAL, claim(stay_end, HOSPITAL, "Inpatient stay, 5 days", 38450.00, "Hospital stay")))
owed_bills.append((stay_end, HOSPITAL, claim(stay_end, HOSPITAL, "Hospital physician care", 2210.00, "Hospital physician care")))


# ------------------------------------------------------------------ visits, procedures, imaging
def visit(d, title, who, where, dx="", note="", procs=(), cat="Doctor visits", billed=None, h=10):
    add(at(d, h, R.choice((0, 15, 30, 45))), "visit", title, f"{who} · {where}",
        {"type": "visit", "who": who, "where": where, "dx": dx, "src": "Demo data", "note": note, "procs": list(procs)})
    owed_bills.append((d, where, claim(d, where, title, billed or round(R.uniform(180, 420), 2), cat)))


def study(d, k, title, impression, where, billed, cat):
    t = at(d, 13, 20)
    add(t, k, title, f"{ONC} · Final · {where}",
        {"type": "result", "order": title, "collected": t[:16].replace("T", " "), "resulted": t[:10], "provider": ONC,
         "status": "Final", "lab": where, "dx": [], "rows": [], "report": [["impression", impression, 0]], "comments": []})
    owed_bills.append((d, where, claim(d, where, title, billed, cat)))


clinic_weights = []
w = 182.0
d = START + timedelta(days=10)
while d < DX - timedelta(days=30):
    visit(d, "Office visit", PCP, CLINIC, "Routine check-up")
    clinic_weights.append([at(d, 10), round(w + R.uniform(-1, 1), 1), CLINIC])
    d += timedelta(days=R.randint(110, 150))
visit(DX - timedelta(days=21), "Office visit", PCP, CLINIC, "Fatigue; low hemoglobin", "Referred to oncology for evaluation.")
visit(DX - timedelta(days=12), "New patient consultation", ONC, CANCER, "Lymph node enlargement", cat="Doctor visits", billed=640)
study(DX - timedelta(days=10), "imaging", "CT chest abdomen pelvis with contrast",
      "Enlarged lymph nodes in the chest and abdomen. No other abnormal findings. (Fictional demo report.)", HOSPITAL, 4120, "Imaging & tests")
study(DX - timedelta(days=5), "procedure", "Lymph node biopsy, core needle",
      "Specimen sent for pathology. Procedure tolerated well. (Fictional demo report.)", HOSPITAL, 6890, "Procedures")
study(DX, "study", "Surgical pathology report",
      "Findings consistent with Hodgkin lymphoma. (Fictional demo report.)", HOSPITAL, 1310, "Lab & pathology")
study(DX + timedelta(days=6), "imaging", "PET/CT skull base to mid thigh",
      "Increased uptake in chest and abdominal lymph nodes. Stage II. (Fictional demo report.)", HOSPITAL, 7450, "Imaging & tests")
study(DX + timedelta(days=13), "procedure", "Implanted port placement",
      "Right chest port placed without complication. (Fictional demo report.)", HOSPITAL, 5280, "Procedures")
for k in range(CYCLES):
    c0 = TX + timedelta(days=k * CYCLE_DAYS)
    visit(c0, f"Treatment visit, cycle {k + 1}", ONC, CANCER, "Hodgkin lymphoma", "Chemotherapy infusion given.",
          ("Chemotherapy infusion",), cat="Chemotherapy", billed=round(R.uniform(9800, 12400), 2), h=8)
study(TX + timedelta(days=2 * CYCLE_DAYS - 3), "imaging", "PET/CT interim",
      "Marked decrease in nodal uptake after two cycles. (Fictional demo report.)", HOSPITAL, 7450, "Imaging & tests")
study(TX_END + timedelta(days=21), "imaging", "PET/CT end of treatment",
      "No residual abnormal uptake. Complete metabolic response. (Fictional demo report.)", HOSPITAL, 7450, "Imaging & tests")
study(TX_END + timedelta(days=60), "procedure", "Implanted port removal",
      "Port removed without complication. (Fictional demo report.)", HOSPITAL, 2140, "Procedures")
d = TX_END + timedelta(days=28)
while d < END - timedelta(days=5):
    visit(d, "Follow-up visit", ONC, CANCER, "Hodgkin lymphoma, in remission")
    d += timedelta(days=90)
if END - timedelta(days=200) > TX_END:
    study(END - timedelta(days=150), "imaging", "CT chest abdomen pelvis with contrast",
          "No evidence of recurrent disease. (Fictional demo report.)", HOSPITAL, 4120, "Imaging & tests")
pt_start = TX_END + timedelta(days=35)                 # 8 weeks of twice-weekly PT (counts as workouts on Health)
for wk in range(8):
    for dd in (0, 3):
        visit(pt_start + timedelta(days=wk * 7 + dd), "Physical therapy session", THER, PT, "Deconditioning after treatment",
              cat="Physical therapy", billed=round(R.uniform(160, 240), 2), h=16)
visit(END - timedelta(days=40), "Office visit", PCP, CLINIC, "Annual physical")
clinic_weights.append([at(END - timedelta(days=40), 10), 176.4, CLINIC])
claim(START + timedelta(days=200), CLINIC, "Office visit", 260.0, "Doctor visits", denied=True)

# ------------------------------------------------------------------ medications and pharmacy fills
MEDS = [("Ondansetron 8 mg tablet", "Take 1 tablet every 8 hours as needed for nausea", TX, TX_END),
        ("Allopurinol 300 mg tablet", "Take 1 tablet daily", TX - timedelta(days=2), TX + timedelta(days=42)),
        ("Acyclovir 400 mg tablet", "Take 1 tablet twice daily", TX, TX_END + timedelta(days=30))]
for name, instr, a, b in MEDS:
    add(at(a, 11), "med", name, f"{instr} · Active", {"type": "med", "instructions": instr, "status": "Active", "src": CANCER})
    f = a
    while f <= b:
        add(at(f, 17), "rx", name, "Pharmacy fill · 30-day supply")
        claim(f, "Corner Pharmacy", name, round(R.uniform(24, 160), 2), "Pharmacy", kind="pharmacy", drug=name)
        f += timedelta(days=30)

# ------------------------------------------------------------------ messages, letters, documents
for i, d in enumerate([DX - timedelta(days=11), DX + timedelta(days=2), TX + timedelta(days=9), stay_end + timedelta(days=3),
                       TX_END + timedelta(days=22), END - timedelta(days=60)]):
    msgs = [{"from": "You", "t": at(d, 19, 5), "body": ["Is there anything I should do before the biopsy?",
             "Thank you for calling with the results. What happens next?", "I have a low fever tonight, 100.2 F. Should I come in?",
             "Home now and feeling better. When is my next lab?", "Got the PET results in the portal - is this good news?",
             "Can I schedule my next scan closer to my follow-up?"][i], "att": []},
            {"from": f"{ONC} (care team)", "t": at(d + timedelta(days=1), 9, 40), "body": ["No food after midnight, and bring a list of your medicines.",
             "We'll meet next week to plan treatment. A nurse will call to schedule.", "Please go to the emergency department now and tell them you are on chemotherapy.",
             "Your next lab is Monday with your cycle visit.", "Yes - no remaining active disease. We'll keep checking every 3 months.",
             "Done - both are on the same morning now."][i], "att": []}]
    add(msgs[0]["t"], "message", ["Biopsy prep", "Next steps", "Fever", "After discharge", "PET results", "Scheduling"][i],
        f"{len(msgs)} messages", {"type": "conversation", "messages": msgs})
add(at(DX + timedelta(days=8), 15), "letter", "Treatment plan summary", CANCER,
    {"type": "letter", "html": "<h3>Treatment plan</h3><p>Six cycles of combination chemotherapy, every 21 days, with labs before each cycle.</p>"
                               "<p><i>This letter is fictional demo content.</i></p>"})
add(at(TX_END + timedelta(days=25), 15), "letter", "End of treatment letter", CANCER,
    {"type": "letter", "html": "<p>Congratulations on completing treatment. Follow-up visits are every three months for the first year.</p>"
                               "<p><i>This letter is fictional demo content.</i></p>"})
add(at(TX_END + timedelta(days=21), 16), "document", "PET/CT end of treatment (PDF)", HOSPITAL,
    {"type": "file", "ext": "pdf", "url": "files/demo-report.pdf"})
# encounters that weren't visits, and a visit titled by its billing code, as portals send them (#236): the app shows
# the first two as chart activity and gives the third a plain name. Last, and without drawing from R, so nothing else
# in the demo moves.
add(at(TX + timedelta(days=CYCLE_DAYS + 2), 11, 5), "visit", "Chart review", f"{ONC} · {CANCER}",
    {"type": "visit", "who": ONC, "where": CANCER, "dx": "", "src": "Demo data", "procs": [],
     "note": "Lab results reviewed before the next cycle; no visit took place."})
add(at(stay_start, 18, 10), "visit", "Telephone", f"Nurse line · {CANCER}",
    {"type": "visit", "who": "Nurse line", "where": CANCER, "dx": "Fever", "src": "Demo data", "procs": [],
     "note": "Called about a fever of 101.8 °F; advised to go to the emergency department."})
add(at(END - timedelta(days=100), 9, 30), "visit", "PR OFFICE/OUTPATIENT ESTABLISHED MOD MDM 30 MIN", f"{PCP} · {CLINIC}",
    {"type": "visit", "who": PCP, "where": CLINIC, "dx": "Follow-up", "src": "Demo data", "procs": [], "note": ""})

# ------------------------------------------------------------------ --big: eight more years before the story (#201)
# A record the size of a long-kept one, for the performance budgets: routine care every couple of weeks, with more
# tests than the story's, monthly prescriptions, claims for all of it, and a small PDF for each scanned document
BIG_DOCS = []   # (file name, title, date): written with the demo's PDF below
if BIG:
    EXTRA = {   # name: (unit, panel, lo, hi, baseline, decimals), all made up for the demo
        "Total Cholesterol": ("mg/dL", "LIPID PANEL", 125, 200, 182, 0), "LDL Cholesterol": ("mg/dL", "LIPID PANEL", 0, 100, 96, 0),
        "HDL Cholesterol": ("mg/dL", "LIPID PANEL", 40, 90, 52, 0), "Triglycerides": ("mg/dL", "LIPID PANEL", 0, 150, 118, 0),
        "Hemoglobin A1c": ("%", "HEMOGLOBIN A1C", 4.0, 5.6, 5.4, 1), "Glucose, Fasting": ("mg/dL", "GLUCOSE", 70, 99, 92, 0),
        "TSH": ("uIU/mL", "THYROID PANEL", 0.4, 4.5, 1.9, 2), "Free T4": ("ng/dL", "THYROID PANEL", 0.8, 1.8, 1.2, 2),
        "Vitamin D, 25-Hydroxy": ("ng/mL", "VITAMIN D", 30, 100, 34, 0), "Vitamin B12": ("pg/mL", "VITAMIN B12", 200, 900, 480, 0),
        "Ferritin": ("ng/mL", "IRON STUDIES", 30, 400, 120, 0), "Iron": ("ug/dL", "IRON STUDIES", 60, 170, 98, 0),
        "TIBC": ("ug/dL", "IRON STUDIES", 250, 450, 330, 0), "Transferrin Saturation": ("%", "IRON STUDIES", 20, 50, 30, 0),
        "Chloride, Blood": ("mmol/L", "BASIC METABOLIC PANEL", 98, 107, 102, 0), "CO2, Blood": ("mmol/L", "BASIC METABOLIC PANEL", 22, 29, 25, 0),
        "BUN": ("mg/dL", "BASIC METABOLIC PANEL", 7, 20, 14, 0), "Calcium": ("mg/dL", "BASIC METABOLIC PANEL", 8.6, 10.3, 9.4, 1),
        "eGFR": ("mL/min/1.73m2", "BASIC METABOLIC PANEL", 60, 120, 96, 0),
        "Albumin": ("g/dL", "HEPATIC PANEL", 3.5, 5.0, 4.3, 1), "Alkaline Phosphatase": ("U/L", "HEPATIC PANEL", 44, 147, 78, 0),
        "Total Protein": ("g/dL", "HEPATIC PANEL", 6.0, 8.3, 7.1, 1), "RBC": ("M/uL", "RED CELL INDICES", 4.5, 5.9, 5.1, 2),
        "Hematocrit": ("%", "RED CELL INDICES", 41, 53, 45, 1), "MCV": ("fL", "RED CELL INDICES", 80, 100, 89, 0),
        "MCH": ("pg", "RED CELL INDICES", 27, 33, 30, 1), "RDW": ("%", "RED CELL INDICES", 11.5, 14.5, 13.1, 1),
        "C-Reactive Protein": ("mg/L", "INFLAMMATION", 0, 5, 1.6, 1), "Sed Rate": ("mm/hr", "INFLAMMATION", 0, 20, 8, 0),
        "Uric Acid": ("mg/dL", "URIC ACID", 3.5, 7.2, 5.6, 1), "Phosphorus": ("mg/dL", "PHOSPHORUS", 2.5, 4.5, 3.4, 1),
        "PSA": ("ng/mL", "PSA", 0, 4, 0.9, 2), "LDH": ("U/L", "LDH", 140, 280, 190, 0),
        "Urine Specific Gravity": ("", "URINALYSIS", 1.005, 1.030, 1.018, 3), "Urine pH": ("", "URINALYSIS", 5.0, 8.0, 6.0, 1),
    }
    LABS.update(EXTRA)
    series.update({n: [] for n in EXTRA})
    ROUTINE = [n for n in EXTRA if EXTRA[n][1] in ("BASIC METABOLIC PANEL", "RED CELL INDICES")]
    OPTIONAL = [n for n in EXTRA if n not in ROUTINE]
    d = FIRST + timedelta(days=5)
    while d < START - timedelta(days=4):   # a draw every two weeks or so: the story's tests, the routine ones, and some others
        draw(d, CORE + ROUTINE + R.sample(OPTIONAL, R.randint(1, 6)))
        d += timedelta(days=R.randint(13, 19))
    d = FIRST + timedelta(days=9)
    while d < START:   # routine visits, about monthly
        visit(d, R.choice(("Office visit", "Office visit", "Follow-up visit", "Annual physical")), PCP, CLINIC,
              R.choice(("Routine check-up", "Blood pressure check", "Seasonal allergies", "Annual physical")))
        clinic_weights.append([at(d, 10), round(182 + R.uniform(-3, 3), 1), CLINIC])
        d += timedelta(days=R.randint(24, 40))
    for name, instr in (("Lisinopril 10 mg tablet", "Take 1 tablet daily"), ("Atorvastatin 20 mg tablet", "Take 1 tablet at bedtime"),
                        ("Cetirizine 10 mg tablet", "Take 1 tablet daily as needed")):
        add(at(FIRST + timedelta(days=20), 11), "med", name, f"{instr} · Active", {"type": "med", "instructions": instr, "status": "Active", "src": CLINIC})
        f = FIRST + timedelta(days=21)
        while f <= END:   # a fill every month, for ten years
            add(at(f, 17), "rx", name, "Pharmacy fill · 30-day supply")
            claim(f, "Corner Pharmacy", name, round(R.uniform(12, 60), 2), "Pharmacy", kind="pharmacy", drug=name)
            f += timedelta(days=30)
    OTHER = (("Dental cleaning", "Example Dental", "Dental"), ("Eye exam", "Clearview Eye Care", "Vision"),
             ("Physical therapy session", PT, "Physical therapy"), ("Urgent care visit", "Harbor Urgent Care", "Urgent care"),
             ("Chiropractic adjustment", "Spine & Joint Chiropractic", "Other care"), ("Office visit", CLINIC, "Doctor visits"))
    n_claims = len(costs)
    d = FIRST + timedelta(days=3)
    while n_claims < 2000:   # other care, until there are 2,000 claims in all
        title, prov, cat = R.choice(OTHER)
        owed_bills.append((d, prov, claim(d, prov, title, round(R.uniform(90, 480), 2), cat)))
        n_claims += 1
        d += timedelta(days=R.randint(2, 6))
        if d >= END:
            d = FIRST + timedelta(days=R.randint(1, 30))
    for i in range(1000):   # 1,000 scanned documents, a small PDF each
        d = FIRST + timedelta(days=i * 3640 // 1000 + R.randint(0, 2))
        title = R.choice(("Visit summary", "Lab report", "Referral letter", "Insurance statement", "Imaging report", "Discharge instructions"))
        name = f"doc-{i + 1:04d}.pdf"
        BIG_DOCS.append((name, title, d))
        add(at(d, 15), "document", f"{title} (PDF)", R.choice((CLINIC, HOSPITAL, CANCER)), {"type": "file", "ext": "pdf", "url": f"files/big/{name}"})
    for i in range(120):   # portal messages
        d = FIRST + timedelta(days=R.randint(0, (START - FIRST).days))
        msgs = [{"from": "You", "t": at(d, 19, 5), "body": "Can I get a refill before my next visit?", "att": []},
                {"from": f"{PCP} (care team)", "t": at(d + timedelta(days=1), 9, 40), "body": "Yes, it has been sent to your pharmacy.", "att": []}]
        add(msgs[0]["t"], "message", "Refill request", f"{len(msgs)} messages", {"type": "conversation", "messages": msgs})

# ------------------------------------------------------------------ out-of-pocket payments
m = date(FIRST.year, FIRST.month, 1)
while m <= END:
    payments.append({"d": m.replace(day=3).isoformat(), "payee": INSURER, "group": "Health insurance premiums",
                     "amount": 186.0, "account": "Checking", "memo": "Monthly premium", "est": False})
    payments.append({"d": m.replace(day=3).isoformat(), "payee": "Example Dental", "group": "Dental insurance premiums",
                     "amount": 21.5, "account": "Checking", "memo": "Monthly premium", "est": False})
    m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
for d, payee, amt in owed_bills:
    if amt > 0 and d + timedelta(days=45) <= END:
        payments.append({"d": (d + timedelta(days=R.randint(28, 45))).isoformat(), "payee": payee, "group": "Medical bills",
                         "amount": amt, "account": "HSA", "memo": "Statement payment", "est": False})
payments.sort(key=lambda p: p["d"])

# ------------------------------------------------------------------ upcoming and problems
upcoming = [{"t": (END + timedelta(days=12)).isoformat(), "title": "Follow-up visit", "who": ONC, "where": CANCER, "dx": "", "procs": []},
            {"t": (END + timedelta(days=12)).isoformat(), "title": "Lab draw", "who": ONC, "where": CANCER, "dx": "", "procs": []},
            {"t": (END + timedelta(days=95)).isoformat(), "title": "CT chest abdomen pelvis", "who": ONC, "where": HOSPITAL, "dx": "", "procs": []}]
problems = [{"name": "Hodgkin lymphoma, in remission", "type": "Diagnosis", "date": DX.isoformat(), "status": "Active", "src": CANCER},
            {"name": "Anemia", "type": "Diagnosis", "date": (DX - timedelta(days=21)).isoformat(), "status": "Resolved", "src": CLINIC}]

# ------------------------------------------------------------------ watch + smart scale (from a month before diagnosis)
wstart = FIRST if BIG else max(START, DX - timedelta(days=30))
met = {k: {} for k in ("steps", "hr_rest", "hr_avg", "hr_min", "hr_max", "hrv", "sleep_h", "sleep_deep_min", "sleep_rem_min",
                       "sleep_light_min", "weight", "body_fat", "lean_mass", "bmr")}
exercise = []
d = wstart
while d <= END:
    k = d.isoformat()
    tired = 0.0
    if TX <= d < TX_END:
        day = (d - TX).days % CYCLE_DAYS
        tired = 0.55 + 0.3 * math.exp(-((day - 4) / 3) ** 2)
    elif TX_END <= d < TX_END + timedelta(days=150):
        tired = 0.5 * (1 - (d - TX_END).days / 150)
    if stay_start <= d <= stay_end:
        tired = 1
    if R.random() < 0.96:                               # a few days without the watch
        met["steps"][k] = max(300, round(R.gauss(8200, 2100) * (1 - 0.7 * tired) * (0.8 if d.weekday() >= 5 else 1)))
        rest = R.gauss(58, 2) + 12 * tired
        met["hr_rest"][k], met["hr_avg"][k] = round(rest), round(rest + R.gauss(13, 2))
        met["hr_min"][k], met["hr_max"][k] = round(rest - R.uniform(4, 8)), round(rest + R.uniform(55, 85) * (1 - 0.4 * tired))
        met["hrv"][k] = round(max(12, R.gauss(46, 6) - 18 * tired))
        sl = max(4.2, R.gauss(7.1, 0.6) + 0.6 * tired)
        deep, rem = sl * R.uniform(0.13, 0.2), sl * R.uniform(0.18, 0.24)
        met["sleep_h"][k] = round(sl, 2)
        met["sleep_deep_min"][k], met["sleep_rem_min"][k] = round(deep * 60), round(rem * 60)
        met["sleep_light_min"][k] = round((sl - deep - rem) * 60)
    target = 182 - (9 if d >= TX else 0) * min(1, max(0, (d - TX).days) / 120) + (4 * min(1, max(0, (d - TX_END).days) / 200) if d >= TX_END else 0)
    if R.random() < 0.45:                               # scale every 2-3 days
        wt = round(target + R.gauss(0, 0.7), 1)
        met["weight"][k], met["body_fat"][k] = wt, round(23.5 + (wt - 176) * 0.35 + R.gauss(0, 0.4), 1)
        met["lean_mass"][k], met["bmr"][k] = round(wt * (1 - met["body_fat"][k] / 100), 1), round(1650 + (wt - 176) * 9)
    if tired < 0.3 and R.random() < 0.3:
        kind = R.choice(("walking", "walking", "biking", "strength_training"))
        mins = R.randint(25, 70)
        s = datetime(d.year, d.month, d.day, R.choice((7, 12, 18)), 0)
        exercise.append({"start": s.isoformat() + "+00:00", "end": (s + timedelta(minutes=mins)).isoformat() + "+00:00",
                         "name": kind, "kind": kind, "minutes": mins})
    d += timedelta(days=1)
META = {"steps": ("Steps", "steps", "Activity"), "hr_rest": ("Resting heart rate", "bpm", "Heart"),
        "hr_avg": ("Heart rate, daily average", "bpm", "Heart"), "hr_min": ("Heart rate, daily low", "bpm", "Heart"),
        "hr_max": ("Heart rate, daily high", "bpm", "Heart"), "hrv": ("HRV (RMSSD)", "ms", "Heart"),
        "sleep_h": ("Sleep", "hours", "Sleep"), "sleep_deep_min": ("Deep sleep", "min", "Sleep"),
        "sleep_rem_min": ("REM sleep", "min", "Sleep"), "sleep_light_min": ("Light sleep", "min", "Sleep"),
        "weight": ("Weight", "lb", "Body"), "body_fat": ("Body fat", "%", "Body"), "lean_mass": ("Lean mass", "lb", "Body"),
        "bmr": ("Basal metabolic rate", "kcal/day", "Body")}
watch = {"updated": at(END, 7), "tz": "America/New_York", "last_payload": at(END, 6, 55),
         "metrics": [{"key": k, "name": META[k][0], "unit": META[k][1], "group": META[k][2], "pts": sorted(met[k].items())} for k in META],
         "exercise": exercise}
if BIG:   # blood glucose (#181), in the shape server/glucose.py writes: a meter most mornings, and a CGM worn for four weeks
    GR = random.Random(END.toordinal() + 181)   # a generator of its own, so the rest of --big is unchanged
    glu = {k: {} for k in ("meter", "avg", "min", "max", "n", "hours", "lt54", "lt70", "in", "gt180")}
    for n in range(3 * 365):   # the three years before the story: a finger-stick on two mornings in three
        d = START - timedelta(days=3 * 365 - n)
        if GR.random() < 0.67:
            glu["meter"][f"{d.isoformat()}T07:{GR.randint(0, 50):02d}"] = GR.randint(84, 126)
    for run in range(12):   # four times a year, a CGM for 28 days, now and then with a gap in its readings
        for n in range(28):
            k = (START - timedelta(days=3 * 365 - run * 91 - n)).isoformat()
            gap = GR.random() < 0.1
            avg, lt70, gt180 = GR.gauss(118, 7), max(0, GR.gauss(1.5, 1.2)), max(0, GR.gauss(6, 4))
            glu["avg"][k], glu["min"][k], glu["max"][k] = round(avg, 1), round(avg - GR.uniform(32, 60)), round(avg + GR.uniform(55, 95))
            glu["n"][k], glu["hours"][k] = (216, 18) if gap else (288, 24)
            glu["lt54"][k], glu["lt70"][k], glu["gt180"][k] = round(lt70 * GR.uniform(0, 0.2), 1), round(lt70, 1), round(gt180, 1)
            glu["in"][k] = round(100 - glu["lt70"][k] - glu["gt180"][k], 1)
    # each CGM day's hours (#303), as glucose.summary keeps them: a count and five percentiles an hour, meals bringing
    # peaks, inside the day's low and high; a day with a gap none from 1 to 7 AM. A generator of their own, so the
    # days above are unchanged
    GH, peak = random.Random(END.toordinal() + 303), lambda h: sum(k * math.exp(-((h - m) / 1.1) ** 2) for m, k in ((8.2, 50), (13, 38), (19.4, 62)))
    curve = [peak(h + .5) for h in range(24)]
    glu["hourly"] = {}
    for k, avg in glu["avg"].items():
        flat = []
        for h in range(24):
            if glu["hours"][k] == 18 and 1 <= h < 7:
                flat.append(0)
                continue
            med, a, b = avg + curve[h] - sum(curve) / 24 + GH.gauss(0, 4), GH.uniform(6, 12), GH.uniform(18, 32)
            ps = [med - b, med - a, med, med + a * GH.uniform(1, 1.4), med + b * GH.uniform(1, 1.5)]
            flat += [12, *sorted(round(min(glu["max"][k], max(glu["min"][k], v))) for v in ps)]
        glu["hourly"][k] = flat
    GMETA = {"meter": ("Blood glucose, meter readings", "mg/dL"), "avg": ("Blood glucose, daily average (CGM)", "mg/dL"),
             "min": ("Blood glucose, daily low (CGM)", "mg/dL"), "max": ("Blood glucose, daily high (CGM)", "mg/dL"),
             "n": ("CGM readings", "readings"), "hours": ("CGM hours with readings", "hours"),
             "lt54": ("CGM readings below 54 mg/dL", "%"), "lt70": ("CGM readings below 70 mg/dL (below 54 included)", "%"),
             "in": ("CGM readings 70–180 mg/dL", "%"), "gt180": ("CGM readings above 180 mg/dL", "%"),
             "hourly": ("CGM readings by hour of the day: how many, and their 5th, 25th, 50th, 75th and 95th percentiles", "mg/dL")}
    watch["metrics"] += [{"key": "glucose_" + k, "name": GMETA[k][0], "unit": GMETA[k][1], "group": "Glucose", "pts": sorted(glu[k].items())} for k in GMETA]

VITALS = []   # --big: the vitals taken at visits, as lab series (below)
if BIG:   # ---- --big: blood pressure at home and at visits (#180), with a generator of its own so nothing above changes
    from zoneinfo import ZoneInfo
    RB, NY, UTC = random.Random(END.toordinal() + 180), ZoneInfo("America/New_York"), ZoneInfo("UTC")
    bp, days = [], {}
    d = FIRST
    while d <= END:
        for h in (7, 21):   # a cuff at home: some mornings, fewer evenings
            if RB.random() < (0.3 if h == 7 else 0.08):
                t = datetime(d.year, d.month, d.day, h, RB.randint(0, 59), tzinfo=NY)
                s, di = round(RB.gauss(126, 6)), round(RB.gauss(80, 4))
                bp.append([t.astimezone(UTC).isoformat(), s, di, round(RB.gauss(68, 5)) if RB.random() < 0.8 else None, "Fictional Cuff", "Health Connect"])
                days.setdefault(d.isoformat(), []).append((s, di))
        d += timedelta(days=1)
    watch["bp_readings"] = bp
    for k, name, f in (("bp_sys", "systolic, daily average", lambda x: sum(s for s, _ in x) / len(x)),
                       ("bp_dia", "diastolic, daily average", lambda x: sum(di for _, di in x) / len(x)),
                       ("bp_sys_max", "systolic, daily highest", lambda x: max(s for s, _ in x)),
                       ("bp_dia_max", "diastolic, daily highest", lambda x: max(di for _, di in x))):
        watch["metrics"].append({"key": k, "name": "Blood pressure, " + name, "unit": "mmHg", "group": "Blood pressure",
                                 "pts": [[day, round(f(x), 2)] for day, x in sorted(days.items())]})
    watch["metrics"].append({"key": "bp_n", "name": "Blood pressure readings", "unit": "readings", "group": "Blood pressure",
                             "pts": [[day, len(x)] for day, x in sorted(days.items())]})
    visits = [t for t, _, org in clinic_weights if org == CLINIC]
    for name, loinc, mu in (("Blood pressure, systolic", "8480-6", 130), ("Blood pressure, diastolic", "8462-4", 83)):
        pts = [[t, v, None, None, "", str(v)] for t in visits for v in [round(RB.gauss(mu, 6))]]
        VITALS.append({"name": name, "unit": "mmHg", "panel": "Vitals", "loinc": loinc, "key": f"loinc:{loinc}", "n": len(pts), "pts": pts, "txt": []})
    # ---- body temperature and blood oxygen (#184): a thermometer during a few colds a year, a watch's oxygen each night
    # for the last three years; at visits, both taken too
    RT, temps, tdays = random.Random(END.toordinal() + 184), [], {}
    d = FIRST
    while d <= END:
        if RT.random() < 3 / 365:   # a cold: a few days of readings, morning and evening
            for k in range(RT.randint(2, 5)):
                for h in (8, 20):
                    t = datetime.combine(d + timedelta(days=k), datetime.min.time()).replace(hour=h, minute=RT.randint(0, 59), tzinfo=NY)
                    c = round(RT.gauss(37.6 if k < 2 else 37.0, 0.3), 1)
                    temps.append([t.astimezone(UTC).isoformat(), c, "Fictional Thermometer", "Health Connect"])
                    tdays.setdefault(t.date().isoformat(), []).append(c)
            d += timedelta(days=6)
        d += timedelta(days=1)
    temps.sort()
    watch["temp_readings"] = temps
    watch["metrics"] += [{"key": "body_temp", "name": "Body temperature, daily average", "unit": "°C", "group": "Body temperature",
                          "pts": [[day, round(sum(x) / len(x), 2)] for day, x in sorted(tdays.items())]},
                         {"key": "body_temp_max", "name": "Body temperature, daily highest", "unit": "°C", "group": "Body temperature",
                          "pts": [[day, max(x)] for day, x in sorted(tdays.items())]}]
    ox = [(day, round(RT.gauss(96.4, 0.8), 1), round(RT.gauss(93.5, 1.2), 1)) for day in
          (d0.isoformat() for d0 in (END - timedelta(days=i) for i in range(3 * 365, -1, -1)))]
    watch["metrics"] += [{"key": "spo2_avg", "name": "SpO2, daily average", "unit": "%", "group": "Oxygen & breathing", "pts": [[x, a] for x, a, _ in ox]},
                         {"key": "spo2_min", "name": "SpO2, daily low", "unit": "%", "group": "Oxygen & breathing", "pts": [[x, min(a, lo)] for x, a, lo in ox]}]
    for name, loinc, unit, mu, sd in (("Temperature", "8310-5", "°F", 98.2, 0.4), ("SpO2 (clinic)", "59408-5", "%", 97.5, 0.8)):
        pts = [[t, v, None, None, "", str(v)] for t in visits for v in [round(RT.gauss(mu, sd), 1)]]
        VITALS.append({"name": name, "unit": unit, "panel": "Vitals", "loinc": loinc, "key": f"loinc:{loinc}", "n": len(pts), "pts": pts, "txt": []})


# ------------------------------------------------------------------ demo PDF (two plain pages)
def pdf(path, pages):
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{' '.join(f'{3 + 2 * i} 0 R' for i in range(len(pages)))}] /Count {len(pages)} >>"]
    font = 3 + 2 * len(pages)
    for i, lines in enumerate(pages):
        body = "BT /F1 12 Tf 72 720 Td 16 TL " + " ".join(f"({l.replace('(', '[').replace(')', ']')}) '" for l in lines) + " ET"
        objs += [f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>",
                 f"<< /Length {len(body)} >>\nstream\n{body}\nendstream"]
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs):
        offs.append(len(out))
        out += f"{i + 1} 0 obj\n{o}\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n"
    with open(path + ".tmp", "w", encoding="latin-1") as f:
        f.write(out)
    os.replace(path + ".tmp", path)


# ------------------------------------------------------------------ write
events.sort(key=lambda e: e["t"])
lab_series = [{"name": n, "unit": LABS[n][0], "panel": LABS[n][1], "n": len(p), "pts": sorted(p), "txt": []} for n, p in series.items()]
lab_series += VITALS
lab_series.sort(key=lambda s: (-s["n"], s["name"]))
data = {"built": at(END, 6), "source": "Sanguity demo data (fictional)", "events": events, "details": details, "labs": lab_series,
        "upcoming": upcoming, "problems": problems, "costs": costs,
        "outOfPocket": {"payments": payments, "exported": END.isoformat(), "range": [FIRST.isoformat(), END.isoformat()],
                        "excluded": "transfers between your own accounts", "balance": {"owed": 0, "asOf": END.isoformat()}},
        "milestones": [{"date": DX.isoformat(), "label": "Diagnosis", "primary": True}, {"date": TX.isoformat(), "label": "Treatment start"},
                       {"date": TX_END.isoformat(), "label": "End of treatment"}],
        "clinicWeights": sorted(clinic_weights)}
WATCH_DIR = WATCH_DIR or os.path.join(OUT, "watch")
os.makedirs(WATCH_DIR, exist_ok=True)
os.makedirs(os.path.join(OUT, "files"), exist_ok=True)
def write_json(path, obj, **kw):   # write then rename, so a running app never reads half a file
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, **kw)
    os.replace(path + ".tmp", path)


write_json(os.path.join(WATCH_DIR, "daily.json"), watch, separators=(",", ":"))
write_json(os.path.join(OUT, "settings.json"),   # count the demo's PT visits as workouts
           {"visitWorkouts": [{"match": "^physical therapy\\b", "name": "Physical therapy", "minutes": 45}]}, indent=2)
pdf(os.path.join(OUT, "files", "demo-report.pdf"),
    [["Northfield General Hospital - Imaging Report", "", "FICTIONAL DEMO DOCUMENT - not a real patient", "",
      f"Exam: PET/CT end of treatment     Date: {(TX_END + timedelta(days=21)).isoformat()}", "",
      "Impression: No residual abnormal uptake.", "Complete metabolic response."],
     ["Page 2", "", "This file exists so the document viewer has something to show.", "It contains no real health information."]])
for name, title, d in BIG_DOCS:   # --big's documents
    os.makedirs(os.path.join(OUT, "files", "big"), exist_ok=True)
    pdf(os.path.join(OUT, "files", "big", name), [[f"{title} - {d.isoformat()}", "", "FICTIONAL DEMO DOCUMENT - not a real patient"]])
write_json(os.path.join(OUT, "data.json"), data, separators=(",", ":"))   # last: the app re-imports when it changes
kinds = {}
for e in events:
    kinds[e["k"]] = kinds.get(e["k"], 0) + 1
print(f"wrote {OUT}: {len(events)} events {kinds}, {len(lab_series)} lab series, {len(costs)} claims, "
      f"{len(payments)} payments, {len(exercise)} workouts, {sum(s['n'] for s in lab_series)} lab results, {FIRST} to {END}")
