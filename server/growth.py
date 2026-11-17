"""Children's growth percentiles on the server (#225): the same as www/js/growth.js (#196) computes on the page, for
what the visit summary and share links show (server/measures.py). The LMS tables are the page's own,
www/js/growth-lms.json (docs/growth-charts.md says where each comes from and its licence), read once when first
needed. The WHO's charts from birth to 2 years, the CDC's from 2 to 20; ages before 2 corrected for a baby born early.
Where a measurement falls, never what that means.

  tables()                   the LMS tables, or None when the file can't be read
  age(day, p)                {"days", "chrono", "corrected"} on that day for person.of()'s p, or None (growthAge)
  chart(days)                "who" up to 2 years (730 days), "cdc" after
  pct(T, key, sex, days, x)  the percentile (0-100) of a measurement: key "weight" (kg), "height" (cm), "bmi"
                             (kg/m², from 2) or "head" (cm, under 2); None when the charts have none for it (growthPct)
  pct_text(p)                "62nd", "below the 1st", "above the 99th" (pctText)
"""
import datetime, json, math, os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "www", "js", "growth-lms.json")
MONTH_DAYS, WHO_TO = 30.4375, 730
CDC_TO = 240 * MONTH_DAYS
SEX_CODE = {"male": "1", "female": "2"}
Z95 = 1.6448536
_T = {}


def tables():
    if "t" not in _T:
        try:
            with open(PATH, encoding="utf-8") as f:
                _T["t"] = json.load(f)
        except (OSError, ValueError):
            _T["t"] = None
    return _T["t"]


def _norm_cdf(z):
    return 0.5 * math.erfc(-z / math.sqrt(2))


def _lms_z(x, L, M, S):
    return math.log(x / M) / S if abs(L) < 1e-12 else ((x / M) ** L - 1) / (L * S)


def _lms_x(z, L, M, S):
    return M * math.exp(S * z) if abs(L) < 1e-12 else M * (1 + L * S * z) ** (1 / L)


def _lms_at(rows, a):
    if not rows or a < rows[0][0] or a > rows[-1][0]:
        return None
    lo, hi = 0, len(rows) - 1
    while hi - lo > 1:
        m = (lo + hi) >> 1
        if rows[m][0] <= a:
            lo = m
        else:
            hi = m
    x, y = rows[lo], rows[hi]
    f = 0 if y[0] == x[0] else (a - x[0]) / (y[0] - x[0])
    return [v + (y[i + 1] - v) * f for i, v in enumerate(x[1:])]


def chart(days):
    return "who" if days <= WHO_TO else "cdc"


def pct(T, key, sex, days, x):
    if not T or sex not in SEX_CODE or days is None or not 0 <= days <= CDC_TO or not isinstance(x, (int, float)) or not x > 0:
        return None
    s = chart(days)
    k = ({"weight": "weight", "height": "length", "head": "head"} if s == "who" else
         {"weight": "weight", "height": "stature", "bmi": "bmi"}).get(key)
    if not k:
        return None
    p = _lms_at(((T.get(s) or {}).get(k) or {}).get(SEX_CODE[sex]), days if s == "who" else days / MONTH_DAYS)
    if not p:
        return None
    L, M, S = p[:3]
    if s == "cdc" and k == "bmi":   # the CDC's 2022 extended BMI-for-age: above the 95th, by the distance from it
        p95 = _lms_x(Z95, L, M, S)
        if x >= p95:
            return 90 + 10 * _norm_cdf((x - p95) / p[3])
    z = _lms_z(x, L, M, S)
    if s == "who" and k != "length" and abs(z) > 3:   # the WHO's: beyond ±3 SD in the distance between the 2 and 3 SD lines
        sign = 1 if z > 0 else -1
        sd3, sd2 = _lms_x(3 * sign, L, M, S), _lms_x(2 * sign, L, M, S)
        z = 3 * sign + (x - sd3) / abs(sd3 - sd2)
    return 100 * _norm_cdf(z)


def _d(v):
    try:
        return datetime.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def age(day, p):
    """The age on a day for growth charts, from About this person (person.of): None without a birth date, before it
    (or before the due date, for a baby born early), or at 20 and over."""
    b, d, due = _d(p.get("birthDate")), _d(day), _d(p.get("dueDate"))
    if b is None or d is None or d < b:
        return None
    if d.year - b.year - ((d.month, d.day) < (b.month, b.day)) >= 20:
        return None
    chrono = (d - b).days
    early = (due - b).days if due and due > b else 0
    if early and chrono <= WHO_TO:
        if chrono < early:
            return None
        return {"days": chrono - early, "chrono": chrono, "corrected": True}
    return {"days": chrono, "chrono": chrono, "corrected": False}


def pct_text(p):
    if p is None:
        return ""
    if p < 0.5:
        return "below the 1st"
    if p >= 99.5:
        return "above the 99th"
    n = int(math.floor(p + 0.5))
    t, u = n % 100, n % 10
    return f"{n}{'th' if 11 <= t <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(u, 'th')}"
