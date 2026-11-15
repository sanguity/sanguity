#!/usr/bin/env python3
"""Generate a fictional demo dataset for Sanguity.

Everything here is made up: the patient, providers, insurer, values and documents. The dataset tells a
two-year story that exercises every tab: routine labs, a diagnosis, six treatment cycles with the usual
blood-count dips, one hospital stay, recovery with physical therapy, insurance claims, payments, and
watch + smart-scale data.

Usage:  python3 -I demo/generate.py [OUT_DIR] [--end YYYY-MM-DD]
        OUT_DIR defaults to www/ and receives data.json, settings.json, watch/daily.json and
        files/demo-report.pdf (all git-ignored). --watch DIR writes daily.json there instead (the Docker
        setup points it at the receiver's public folder). The same --end date always produces the same data.
"""
import json, math, os, random, sys
from datetime import date, datetime, timedelta

argv = sys.argv[1:]
WATCH_DIR = None
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
    for order, members in (("CBC WITH DIFFERENTIAL", [n for n in names if n in CBC]),
                           ("COMPREHENSIVE METABOLIC PANEL", [n for n in names if n in CMP]),
                           ("MAGNESIUM", [n for n in names if n == "Magnesium"])):
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
    add(t, "lab", f"{len(ids)} lab result{'s' if len(ids) != 1 else ''}", ", ".join(orders), {"type": "labday", "ids": ids}, abn=abn)
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
    names = list(LABS) if kind == "full" else CBC + ["ALT", "AST", "Total Bilirubin"] if kind == "liver" else CBC
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

# ------------------------------------------------------------------ out-of-pocket payments
m = date(START.year, START.month, 1)
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
wstart = max(START, DX - timedelta(days=30))
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
lab_series.sort(key=lambda s: (-s["n"], s["name"]))
data = {"built": at(END, 6), "source": "Sanguity demo data (fictional)", "events": events, "details": details, "labs": lab_series,
        "upcoming": upcoming, "problems": problems, "costs": costs,
        "outOfPocket": {"payments": payments, "exported": END.isoformat(), "range": [START.isoformat(), END.isoformat()],
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
write_json(os.path.join(OUT, "data.json"), data, separators=(",", ":"))   # last: the app re-imports when it changes
kinds = {}
for e in events:
    kinds[e["k"]] = kinds.get(e["k"], 0) + 1
print(f"wrote {OUT}: {len(events)} events {kinds}, {len(lab_series)} lab series, {len(costs)} claims, "
      f"{len(payments)} payments, {len(exercise)} workouts, {START} to {END}")
