#!/usr/bin/env python3
"""Receiver for Life Dashboard Companion (Health Connect) webhooks.

- Verifies X-Signature (HMAC-SHA256 of the raw body) with the secret in $SECRET_FILE.
- Archives every payload, gzipped, under $DATA/raw/YYYY/MM/DD/ (the source of truth).
- Folds records into $DATA/watch.db (SQLite) following docs/webhook.md:
  dedupe on uuid / sequence, bucket replace-vs-combine, deletions.
- Writes $DATA/public/daily.json (per-day summaries) for Sanguity.

Run:  python3 receiver.py            (serve on :8080)
      python3 receiver.py --reprocess (rebuild watch.db and daily.json from raw/)
Stdlib only.
"""
import gzip, hashlib, hmac, json, os, queue, sqlite3, sys, threading, time, traceback
from datetime import datetime, timezone, date as Date
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from zoneinfo import ZoneInfo

DATA = os.environ.get("DATA", "/data")
SECRET_FILE = os.environ.get("SECRET_FILE", "/run/secret/watch_secret")
DEFAULT_TZ = os.environ.get("TZ_DEFAULT", "UTC")
PORT = int(os.environ.get("PORT", "8080"))
MAX_BODY = 32 * 1024 * 1024

SAMPLE_TYPES = {  # payload key -> value field(s)
    "heart_rate": ("bpm", None),
    "resting_heart_rate": ("bpm", None),
    "heart_rate_variability": ("heart_rate_variability_millis", None),
    "oxygen_saturation": ("percentage", None),
    "respiratory_rate": ("rate", None),
    "skin_temperature": ("delta_celsius", None),
    "body_temperature": ("celsius", None),
    "blood_pressure": ("systolic", "diastolic"),
    "weight": ("kilograms", None),
    "body_fat": ("percentage", None),
    "lean_body_mass": ("kilograms", None),
    "bone_mass": ("kilograms", None),
    "body_water_mass": ("kilograms", None),
    "basal_metabolic_rate": ("kilocalories_per_day", None),
}
LB = 2.20462
ASLEEP = {"sleeping", "light", "deep", "rem"}


def log(*a):
    print(datetime.now().isoformat(timespec="seconds"), *a, flush=True)


def secret():
    with open(SECRET_FILE, "rb") as f:
        return f.read().strip()


def parse_t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS processed(seq INTEGER, digest TEXT PRIMARY KEY, at TEXT);
        CREATE TABLE IF NOT EXISTS samples(type TEXT, uuid TEXT, t INTEGER, day TEXT, v REAL, v2 REAL, src TEXT,
            PRIMARY KEY(type, uuid));
        CREATE INDEX IF NOT EXISTS samples_day ON samples(type, day);
        CREATE TABLE IF NOT EXISTS buckets(type TEXT, start INTEGER, end_ INTEGER, day TEXT, n INTEGER,
            avg REAL, min REAL, max REAL, total REAL, PRIMARY KEY(type, start));
        CREATE TABLE IF NOT EXISTS sleep(uuid TEXT PRIMARY KEY, end_t INTEGER, day TEXT, duration INTEGER, stages TEXT, src TEXT);
        CREATE TABLE IF NOT EXISTS exercise(uuid TEXT PRIMARY KEY, start_t INTEGER, end_t INTEGER, day TEXT, type TEXT,
            name TEXT, title TEXT, duration INTEGER, src TEXT);
        CREATE TABLE IF NOT EXISTS totals(day TEXT PRIMARY KEY, seq INTEGER, steps REAL, distance REAL,
            active_cal REAL, total_cal REAL);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        """)

    def meta(self, k, v=None):
        if v is None:
            r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return r[0] if r else None
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, str(v)))

    def ingest(self, p, digest):
        if p.get("test") or p.get("source") not in ("health_connect", None):
            return "skipped"
        if self.db.execute("SELECT 1 FROM processed WHERE digest=?", (digest,)).fetchone():
            return "duplicate"
        tz = ZoneInfo(p.get("timezone") or self.meta("tz") or DEFAULT_TZ)
        if p.get("timezone"):
            self.meta("tz", p["timezone"])
        day = lambda dt: dt.astimezone(tz).date().isoformat()
        seq = p.get("sequence") or 0
        c = self.db.cursor()
        n = 0
        for key, (f1, f2) in SAMPLE_TYPES.items():
            for r in p.get(key) or []:
                if "bucket_start" in r:
                    n += self._bucket(c, key, r, day)
                    continue
                if f1 not in r or "time" not in r:
                    continue
                t = parse_t(r["time"])
                c.execute("INSERT OR REPLACE INTO samples VALUES(?,?,?,?,?,?,?)",
                          (key, r.get("uuid") or f"{key}@{r['time']}", int(t.timestamp()), day(t),
                           r[f1], r.get(f2) if f2 else None, r.get("source")))
                n += 1
        for key in ("steps", "distance", "active_calories", "total_calories"):
            for r in p.get(key) or []:
                if "bucket_start" in r:
                    n += self._bucket(c, key, r, day)
        for r in p.get("sleep") or []:
            end = parse_t(r["session_end_time"])
            c.execute("INSERT OR REPLACE INTO sleep VALUES(?,?,?,?,?,?)",
                      (r.get("uuid") or f"sleep@{r['session_end_time']}", int(end.timestamp()), day(end),
                       r.get("duration_seconds") or 0, json.dumps(r.get("stages") or []), r.get("source")))
            n += 1
        for r in p.get("exercise") or []:
            s, e = parse_t(r["start_time"]), parse_t(r["end_time"])
            c.execute("INSERT OR REPLACE INTO exercise VALUES(?,?,?,?,?,?,?,?,?)",
                      (r.get("uuid") or f"ex@{r['start_time']}", int(s.timestamp()), int(e.timestamp()), day(s),
                       r.get("type"), r.get("name"), r.get("title"), r.get("duration_seconds"), r.get("source")))
            n += 1
        for t in p.get("daily_totals") or []:
            old = c.execute("SELECT seq FROM totals WHERE day=?", (t["date"],)).fetchone()
            if old is None or seq >= (old[0] or 0):
                c.execute("INSERT OR REPLACE INTO totals VALUES(?,?,?,?,?,?)",
                          (t["date"], seq, t.get("steps"), t.get("distance_meters"),
                           t.get("active_calories"), t.get("total_calories")))
        for d in p.get("deleted_records") or []:
            typ, u = d.get("type"), d.get("uuid")
            if not u:
                continue
            if typ in SAMPLE_TYPES:
                c.execute("DELETE FROM samples WHERE type=? AND (uuid=? OR uuid LIKE ?)", (typ, u, u + "#%"))
            elif typ == "sleep":
                c.execute("DELETE FROM sleep WHERE uuid=?", (u,))
            elif typ == "exercise":
                c.execute("DELETE FROM exercise WHERE uuid=?", (u,))
        c.execute("INSERT INTO processed VALUES(?,?,?)", (seq, digest, datetime.now(timezone.utc).isoformat()))
        self.meta("last_payload", p.get("timestamp") or "")
        self.db.commit()
        return f"{n} records"

    def _bucket(self, c, key, r, day):
        s, e = parse_t(r["bucket_start"]), parse_t(r["bucket_end"])
        k = (key, int(s.timestamp()))
        old = c.execute("SELECT n, avg, min, max, total FROM buckets WHERE type=? AND start=?", k).fetchone()
        n, avg, mn, mx, tot = r.get("sample_count") or 0, r.get("avg"), r.get("min"), r.get("max"), r.get("total")
        if old and not r.get("complete"):
            on, oavg, omn, omx, otot = old
            tn = (on or 0) + n
            if avg is not None and oavg is not None and tn:
                avg = (oavg * (on or 0) + avg * n) / tn
            avg = avg if avg is not None else oavg
            mn = min(x for x in (mn, omn) if x is not None) if (mn is not None or omn is not None) else None
            mx = max(x for x in (mx, omx) if x is not None) if (mx is not None or omx is not None) else None
            tot = (tot or 0) + (otot or 0) if (tot is not None or otot is not None) else None
            n = tn
        c.execute("INSERT OR REPLACE INTO buckets VALUES(?,?,?,?,?,?,?,?,?)",
                  (key, k[1], int(e.timestamp()), day(s), n, avg, mn, mx, tot))
        return 1

    # ------------------------------------------------------------ daily summary
    def summary(self):
        q = self.db.execute
        out = {}

        def put(key, d, v):
            if v is not None:
                out.setdefault(key, {})[d] = round(v, 2)

        # measured series: weighted mean over raw samples + buckets
        for typ, kavg, kmin, kmax in (("heart_rate", "hr_avg", "hr_min", "hr_max"),
                                      ("heart_rate_variability", "hrv", None, None),
                                      ("oxygen_saturation", "spo2_avg", "spo2_min", None),
                                      ("respiratory_rate", "resp", None, None),
                                      ("skin_temperature", "skin_temp", None, None)):
            acc = {}
            for d, s, n, mn, mx in q("SELECT day, SUM(v), COUNT(*), MIN(v), MAX(v) FROM samples WHERE type=? GROUP BY day", (typ,)):
                acc[d] = [s, n, mn, mx]
            for d, s, n, mn, mx in q("SELECT day, SUM(avg*n), SUM(n), MIN(min), MAX(max) FROM buckets WHERE type=? GROUP BY day", (typ,)):
                a = acc.setdefault(d, [0, 0, None, None])
                a[0] += s or 0; a[1] += n or 0
                a[2] = mn if a[2] is None else (a[2] if mn is None else min(a[2], mn))
                a[3] = mx if a[3] is None else (a[3] if mx is None else max(a[3], mx))
            for d, (s, n, mn, mx) in acc.items():
                if n:
                    put(kavg, d, s / n)
                if kmin:
                    put(kmin, d, mn)
                if kmax:
                    put(kmax, d, mx)
        for d, v in q("SELECT day, AVG(v) FROM samples WHERE type='resting_heart_rate' GROUP BY day"):
            put("hr_rest", d, v)
        # body composition (smart scale): one value per day, pounds for masses
        for typ, key, f in (("weight", "weight", LB), ("body_fat", "body_fat", 1), ("lean_body_mass", "lean_mass", LB),
                            ("bone_mass", "bone_mass", LB), ("body_water_mass", "water_mass", LB), ("basal_metabolic_rate", "bmr", 1)):
            for d, v in q("SELECT day, AVG(v) FROM samples WHERE type=? GROUP BY day", (typ,)):
                put(key, d, v * f if v is not None else None)
        for d, steps, dist, act in q("SELECT day, steps, distance, active_cal FROM totals"):
            put("steps", d, steps)
            put("distance_km", d, dist / 1000 if dist is not None else None)
            put("active_cal", d, act)
        sl = {}
        for d, dur, stages in q("SELECT day, duration, stages FROM sleep"):
            st = json.loads(stages or "[]")
            a = sl.setdefault(d, {"asleep": 0, "deep": 0, "rem": 0, "light": 0, "awake": 0})
            if st:
                for s in st:
                    sec = s.get("duration_seconds") or 0
                    if s.get("stage") in ASLEEP:
                        a["asleep"] += sec
                    if s.get("stage") in ("deep", "rem", "light"):
                        a[s["stage"]] += sec
                    if s.get("stage") in ("awake", "awake_in_bed"):
                        a["awake"] += sec
            else:
                a["asleep"] += dur or 0
        for d, a in sl.items():
            put("sleep_h", d, a["asleep"] / 3600)
            for k in ("deep", "rem", "light", "awake"):
                if a[k]:
                    put(f"sleep_{k}_min", d, a[k] / 60)
        exercises = [{"start": datetime.fromtimestamp(s, timezone.utc).isoformat(), "end": datetime.fromtimestamp(e, timezone.utc).isoformat(),
                      "name": (title or name or f"type {typ}"), "kind": name or typ, "minutes": round((dur or (e - s)) / 60)}
                     for s, e, typ, name, title, dur in q("SELECT start_t, end_t, type, name, title, duration FROM exercise ORDER BY start_t")]
        META = {
            "steps": ("Steps", "steps", "Activity"), "distance_km": ("Distance", "km", "Activity"),
            "active_cal": ("Active calories", "kcal", "Activity"),
            "hr_rest": ("Resting heart rate", "bpm", "Heart"), "hr_avg": ("Heart rate, daily average", "bpm", "Heart"),
            "hr_min": ("Heart rate, daily low", "bpm", "Heart"), "hr_max": ("Heart rate, daily high", "bpm", "Heart"),
            "hrv": ("HRV (RMSSD)", "ms", "Heart"),
            "spo2_avg": ("SpO2, daily average", "%", "Oxygen & breathing"), "spo2_min": ("SpO2, daily low", "%", "Oxygen & breathing"),
            "resp": ("Respiratory rate", "breaths/min", "Oxygen & breathing"),
            "sleep_h": ("Sleep", "hours", "Sleep"), "sleep_deep_min": ("Deep sleep", "min", "Sleep"),
            "sleep_rem_min": ("REM sleep", "min", "Sleep"), "sleep_light_min": ("Light sleep", "min", "Sleep"),
            "sleep_awake_min": ("Awake during sleep", "min", "Sleep"),
            "skin_temp": ("Skin temperature change", "°C", "Other"),
            "weight": ("Weight", "lb", "Body"), "body_fat": ("Body fat", "%", "Body"), "lean_mass": ("Lean mass", "lb", "Body"),
            "bone_mass": ("Bone mass", "lb", "Body"), "water_mass": ("Body water", "lb", "Body"),
            "bmr": ("Basal metabolic rate", "kcal/day", "Body"),
        }
        metrics = [{"key": k, "name": META[k][0], "unit": META[k][1], "group": META[k][2],
                    "pts": sorted(out[k].items())} for k in META if k in out]
        return {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tz": self.meta("tz") or DEFAULT_TZ,
                "last_payload": self.meta("last_payload"), "metrics": metrics, "exercise": exercises}

    def write_summary(self):
        pub = os.path.join(DATA, "public")
        os.makedirs(pub, exist_ok=True)
        tmp = os.path.join(pub, "daily.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.summary(), f, separators=(",", ":"))
        os.replace(tmp, os.path.join(pub, "daily.json"))


def archive(body):
    now = datetime.now(timezone.utc)
    d = os.path.join(DATA, "raw", now.strftime("%Y/%m/%d"))
    os.makedirs(d, exist_ok=True)
    digest = hashlib.sha256(body).hexdigest()
    path = os.path.join(d, f"{now.strftime('%H%M%S%f')}-{digest[:12]}.json.gz")
    with gzip.open(path, "wb") as f:
        f.write(body)
    return digest


work = queue.Queue()


def worker(store):
    dirty = False
    while True:
        try:
            item = work.get(timeout=5)
        except queue.Empty:
            if dirty:
                try:
                    store.write_summary(); log("summary written")
                except Exception:
                    traceback.print_exc()
                dirty = False
            continue
        body, digest = item
        try:
            res = store.ingest(json.loads(body), digest)
            log("ingested", res)
            dirty = dirty or res not in ("skipped", "duplicate")
        except Exception:
            log("FAILED to process payload", digest[:12]); traceback.print_exc()


class H(BaseHTTPRequestHandler):
    server_version = "watch-receiver"

    def _send(self, code, msg=b""):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(204)
        self._send(404)

    def do_POST(self):
        if self.path.split("?")[0] not in ("/ingest", "/ingest/"):
            return self._send(404)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._send(413 if n > MAX_BODY else 400)
        body = self.rfile.read(n)
        expected = "sha256=" + hmac.new(secret(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.encode(), (self.headers.get("X-Signature") or "").encode()):
            log("rejected: bad signature from", self.client_address[0])
            return self._send(401)
        try:
            json.loads(body)
        except ValueError:
            return self._send(400)
        digest = archive(body)
        work.put((body, digest))
        self._send(200, b"ok")

    def log_message(self, *a):
        pass


def reprocess():
    db = os.path.join(DATA, "watch.db")
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db + ext):
            os.remove(db + ext)
    store = Store(db)
    files = sorted(os.path.join(r, f) for r, _, fs in os.walk(os.path.join(DATA, "raw")) for f in fs if f.endswith(".json.gz"))
    for p in files:
        with gzip.open(p, "rb") as f:
            body = f.read()
        try:
            store.ingest(json.loads(body), hashlib.sha256(body).hexdigest())
        except Exception as e:
            log("skip", p, e)
    store.write_summary()
    log(f"reprocessed {len(files)} payloads")


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    if "--reprocess" in sys.argv:
        reprocess(); sys.exit(0)
    secret()  # fail fast when the secret is missing
    store = Store(os.path.join(DATA, "watch.db"))
    if not os.path.exists(os.path.join(DATA, "public", "daily.json")):
        store.write_summary()
    threading.Thread(target=worker, args=(store,), daemon=True).start()
    log(f"listening on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
