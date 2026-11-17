#!/usr/bin/env python3
"""Receiver for the phone apps' uploads, on one address: Life Dashboard Companion (Health Connect, Android) webhooks,
and Health Auto Export (Apple Health, iPhone) REST API automations (#148).

- Verifies X-Signature (HMAC-SHA256 of the raw body; Life Dashboard), or the secret itself sent as
  "Authorization: Bearer <secret>" (Health Auto Export, which can add a header but not sign), with one profile's
  secret (below), which says whose upload it is.
- Archives every payload, gzipped, under <slot>/raw/YYYY/MM/DD/ (the source of truth).
- Folds records into an SQLite database following docs/webhook.md:
  dedupe on uuid / sequence, bucket replace-vs-combine, deletions.
- Health Auto Export's JSON ({"data": {"metrics": [...], "workouts": [...]}}) is kept reading by reading, keyed by
  metric, source and time (a whole sleep night by source and day, a workout by start, end and type), so repeated and
  overlapping uploads count once; days are each reading's own local date. The day's figures are made with the Apple
  Health importer's own mapping and aggregation (server/importers/apple_health.py), so an iPhone gives the same
  Health tab as an uploaded export.xml, HRV as SDNN. On a day both apps sent one metric, Health Connect's value wins.
- Blood glucose from both apps (#181, server/glucose.py): every reading kept, meter readings passed on as they are,
  a continuous glucose monitor's summarised per day (average, low, high, hours with readings, time in ranges).
- Home blood pressure (#180) and body temperature (#184) are kept reading by reading from both apps, a reading sent
  again (or by both) counted once, and summed up by day; the summary lists the readings too.
- Writes per-day summaries for Sanguity: $DATA/public/daily.json, or public/profiles/<slot>/daily.json.

One secret per profile (#46). The app makes each profile's secret on its Settings page and writes them all, sealed,
to $KEYS_FILE (server/watch_keys.py); an upload goes to the profile whose secret signed it. Each profile has a slot:
"" is the receiver's top folder ($DATA/watch.db, $DATA/raw/, public/daily.json), as before #46; any other is the
profile's id, with its own $DATA/profiles/<slot>/watch.db and raw/, and public/profiles/<slot>/daily.json. The
install's single secret from before, $SECRET_FILE, still works for slot "" until that profile (the first) makes one
of its own in the app ("legacy": false in $KEYS_FILE), and always while there's no $KEYS_FILE.

Encrypted at rest with the app's key ($SANGUITY_KEY_FILE, see server/crypto.py): every file here is sealed, so
it runs on the app's image and imports server.crypto. The database is kept in memory and saved as a sealed
snapshot, $DATA/watch.db, once uploads have been folded in; on start it is loaded back and the uploads archived
since the last snapshot are folded in again (a crash loses nothing raw/ holds). Plaintext files from before
encryption, and files sealed with an older key, are re-sealed with the current key on start.

Run:  python3 receiver.py            (serve on :8080)
      python3 receiver.py --reprocess (rebuild watch.db and daily.json from raw/; with the receiver stopped)
"""
import gzip, hashlib, hmac, json, os, queue, sqlite3, sys, threading, time, traceback
from datetime import datetime, timedelta, timezone, date as Date
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.dirname(HERE)):   # /app in the container, the repository's root otherwise
    if os.path.isfile(os.path.join(_d, "server", "crypto.py")):
        sys.path.insert(0, _d)
        break
os.environ.setdefault("SANGUITY_KEY_FILE", "/run/secrets/sanguity/data_key")
from server import crypto   # noqa: E402  (the app's own encryption and key file)
from server.importers import apple_health as ah   # noqa: E402  (Apple Health's mapping, for Health Auto Export)
from server import glucose   # noqa: E402  (blood glucose, meters and CGMs, #181)

DATA = os.environ.get("DATA", "/data")
SECRET_FILE = os.environ.get("SECRET_FILE", "/run/secret/watch_secret")
KEYS_FILE = os.environ.get("KEYS_FILE") or os.path.join(DATA, "keys", "receiver-secrets.json")
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

# Health Auto Export metric name -> the HealthKit type it reads, then mapped as the Apple Health importer maps it
HAE_QUANTITY = {
    "step_count": "HKQuantityTypeIdentifierStepCount",
    "walking_running_distance": "HKQuantityTypeIdentifierDistanceWalkingRunning",
    "active_energy": "HKQuantityTypeIdentifierActiveEnergyBurned",
    "heart_rate": "HKQuantityTypeIdentifierHeartRate",
    "resting_heart_rate": "HKQuantityTypeIdentifierRestingHeartRate",
    "heart_rate_variability": "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    "heart_rate_variability_sdnn": "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    "blood_oxygen_saturation": "HKQuantityTypeIdentifierOxygenSaturation",
    "respiratory_rate": "HKQuantityTypeIdentifierRespiratoryRate",
    "weight_body_mass": "HKQuantityTypeIdentifierBodyMass",
    "body_mass": "HKQuantityTypeIdentifierBodyMass",
    "body_fat_percentage": "HKQuantityTypeIdentifierBodyFatPercentage",
    "lean_body_mass": "HKQuantityTypeIdentifierLeanBodyMass",
}
HAE_UNITS = {"rate": {"bpm": 1, "breaths/min": 1}, "count": {"steps": 1}}   # spellings besides HealthKit's own
HAE_STAGE = {   # a sleep segment's "value" -> the receiver's stage; in bed overlaps the others and isn't sleep
    "core": "light", "deep": "deep", "rem": "rem", "awake": "awake", "asleep": "sleeping", "unspecified": "sleeping",
    "in bed": None, "inbed": None}

# ---- home readings kept one by one (#180, #184): blood pressure and body temperature, from both phone apps
HAE_VITALS = {"blood_pressure", "body_temperature"}   # Health Auto Export metrics kept reading by reading (Store._hae_vitals)
VITALS_META = {   # the daily summary of them, after the receiver's other metrics
    "bp_sys": ("Blood pressure, systolic, daily average", "mmHg", "Blood pressure"),
    "bp_dia": ("Blood pressure, diastolic, daily average", "mmHg", "Blood pressure"),
    "bp_sys_max": ("Blood pressure, systolic, daily highest", "mmHg", "Blood pressure"),
    "bp_dia_max": ("Blood pressure, diastolic, daily highest", "mmHg", "Blood pressure"),
    "bp_n": ("Blood pressure readings", "readings", "Blood pressure"),
    "body_temp": ("Body temperature, daily average", "°C", "Body temperature"),
    "body_temp_max": ("Body temperature, daily highest", "°C", "Body temperature"),
}
HAE_TEMP = {"degc": "C", "°c": "C", "c": "C", "cel": "C", "degf": "F", "°f": "F", "f": "F", "[degf]": "F"}   # a unit's spellings
APP = {"health_connect": "Health Connect", "health_auto_export": "Apple Health"}   # which phone app sent a reading


def number(v):
    """A reading's value as a float, or None when it isn't a number."""
    if isinstance(v, dict):   # Health Auto Export may send {"qty": 72, "units": "bpm"}
        v = v.get("qty")
    if isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def pulse_of(r):
    """A blood pressure reading's pulse (bpm), when the app sent one with it."""
    for k in ("pulse", "pulse_bpm", "bpm", "heart_rate"):
        if (v := number(r.get(k))) is not None:
            return v
    return None


def compact(v):
    """128.0 -> 128, 36.65 -> 36.65: the summary's numbers as short as they can be written."""
    v = round(v, 2)
    return int(v) if v == int(v) else v


def log(*a):
    print(datetime.now().isoformat(timespec="seconds"), *a, flush=True)


def secret():
    """The install's single secret from before #46 (slot ""), or None."""
    try:
        with open(SECRET_FILE, "rb") as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


_keys = {"sig": False, "doc": None}


def keys():
    """[(slot, secret)] the receiver takes now: every profile's from $KEYS_FILE (read again when it changes), and
    the install's single secret for slot "" while that's still in use."""
    try:
        st = os.stat(KEYS_FILE)
        sig = (st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        sig = None
    if sig != _keys["sig"]:
        doc = None
        if sig is not None:
            try:
                doc = json.loads(crypto.read_file(KEYS_FILE))
            except Exception as e:   # unreadable: keep what was read last, and try again next time
                log("couldn't read", KEYS_FILE, type(e).__name__, e)
                return _from(_keys["doc"])
        _keys.update(sig=sig, doc=doc)
    return _from(_keys["doc"])


def _from(doc):
    out = []
    if isinstance(doc, dict):
        for p in doc.get("profiles") or []:
            slot, sec = str(p.get("slot", "")), p.get("secret")
            if (slot == "" or slot.isdigit()) and isinstance(sec, str) and sec:
                out.append((slot, sec.encode()))
    if not isinstance(doc, dict) or doc.get("legacy", True):
        if (old := secret()):
            out.append(("", old))
    return out


def slot_dir(slot):
    return DATA if slot == "" else os.path.join(DATA, "profiles", slot)


def summary_file(slot):
    if slot == "":
        return os.path.join(DATA, "public", "daily.json")
    return os.path.join(DATA, "public", "profiles", slot, "daily.json")


def slots_on_disk():
    """"" and every profile slot that has a folder."""
    top = os.path.join(DATA, "profiles")
    return [""] + sorted((n for n in (os.listdir(top) if os.path.isdir(top) else ()) if n.isdigit()), key=int)


def parse_t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def zone(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in ("UTC", "Etc/UTC"):   # a system without a time zone database (Windows without tzdata)
            return timezone.utc
        raise


STAMP = "%Y%m%d%H%M%S%f"   # raw/ file names sort by this (YYYY/MM/DD/HHMMSSffffff-...)


def raw_files(slot=""):
    """(stamp, path) of every archived upload of a slot, oldest first."""
    top = os.path.join(slot_dir(slot), "raw")
    out = []
    for r, _, fs in os.walk(top):
        for f in fs:
            if f.endswith(".json.gz"):
                p = os.path.join(r, f)
                out.append(("".join(os.path.relpath(p, top).split(os.sep))[:20], p))
    return sorted(out)


def read_raw(path):
    return gzip.decompress(crypto.read_file(path))   # sealed, or plaintext from before encryption


def migrate():
    """Seal what is still plaintext, or sealed with an older key, with the current key. Returns the count."""
    return sum(_migrate(slot) for slot in slots_on_disk())


def _migrate(slot):
    n = 0
    db = os.path.join(slot_dir(slot), "watch.db")
    if os.path.isfile(db) and not crypto.file_is_encrypted(db):   # an SQLite file from before encryption
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=DELETE")   # folds in and removes its -wal; an in-memory copy can't use WAL
        image = con.serialize()
        con.close()
        crypto.write_file(db, image)
        for ext in ("-wal", "-shm"):
            if os.path.exists(db + ext):
                os.remove(db + ext)
        n += 1
    paths = [p for _, p in raw_files(slot)] + [db, summary_file(slot)]
    for p in paths:
        if os.path.isfile(p):
            with open(p, "rb") as f:
                head = f.read(8)
            if crypto.needs_resealing(head):
                crypto.write_file(p, crypto.read_file(p))
                n += 1
    return n


class Store:
    """One slot's database, in memory; save() writes it to its watch.db sealed, and a new Store loads it from there."""
    def __init__(self, slot=""):
        self.slot = slot
        os.makedirs(slot_dir(slot), exist_ok=True)
        self.path = path = os.path.join(slot_dir(slot), "watch.db")
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.loaded = os.path.isfile(path)
        if self.loaded:
            self.db.deserialize(crypto.read_file(path))
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS processed(seq INTEGER, digest TEXT PRIMARY KEY, at TEXT);
        CREATE TABLE IF NOT EXISTS samples(type TEXT, uuid TEXT, t INTEGER, day TEXT, v REAL, v2 REAL, src TEXT,
            v3 REAL, PRIMARY KEY(type, uuid));
        CREATE INDEX IF NOT EXISTS samples_day ON samples(type, day);
        CREATE TABLE IF NOT EXISTS buckets(type TEXT, start INTEGER, end_ INTEGER, day TEXT, n INTEGER,
            avg REAL, min REAL, max REAL, total REAL, PRIMARY KEY(type, start));
        CREATE TABLE IF NOT EXISTS sleep(uuid TEXT PRIMARY KEY, end_t INTEGER, day TEXT, duration INTEGER, stages TEXT, src TEXT);
        CREATE TABLE IF NOT EXISTS exercise(uuid TEXT PRIMARY KEY, start_t INTEGER, end_t INTEGER, day TEXT, type TEXT,
            name TEXT, title TEXT, duration INTEGER, src TEXT);
        CREATE TABLE IF NOT EXISTS totals(day TEXT PRIMARY KEY, seq INTEGER, steps REAL, distance REAL,
            active_cal REAL, total_cal REAL);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS hae_points(key TEXT, src TEXT, t INTEGER, day TEXT, v REAL, mn REAL, mx REAL,
            PRIMARY KEY(key, src, t));
        CREATE TABLE IF NOT EXISTS hae_sleep(src TEXT, start TEXT, end_ TEXT, stage TEXT, night TEXT,
            PRIMARY KEY(src, start, stage));
        """)
        self._glucose_init()   # blood glucose (#181), below
        self._vitals_tables()   # home blood pressure and body temperature (#180, #184)

    def meta(self, k, v=None):
        if v is None:
            r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return r[0] if r else None
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, str(v)))

    def save(self):
        """Write the database to disk, sealed (atomically: a crash leaves the previous snapshot)."""
        self.meta("saved", datetime.now(timezone.utc).strftime(STAMP))
        self.db.commit()
        crypto.write_file(self.path, self.db.serialize())

    def catch_up(self):
        """Fold in the uploads archived since the last snapshot (all of raw/ for a new database). Returns how many
        were new. Those already in it are recognised by their digest and skipped."""
        n = self._glucose_backfill()   # an existing database's glucose, once (#181)
        saved = self.meta("saved")
        if self.loaded and not saved:
            return n   # a database from before snapshots: it was written as uploads came in, so it is current
        since = (datetime.strptime(saved, STAMP) - timedelta(minutes=10)).strftime(STAMP) if saved else ""
        for stamp, p in raw_files(self.slot):
            if stamp < since:
                continue
            try:
                body = read_raw(p)
                n += self.ingest(json.loads(body), hashlib.sha256(body).hexdigest()) not in ("skipped", "duplicate")
            except Exception as e:
                log("skip", p, e)
        return n

    def ingest(self, p, digest):
        hae = is_hae(p)
        if not hae and (p.get("test") or p.get("source") not in ("health_connect", None)):
            return "skipped"
        if self.db.execute("SELECT 1 FROM processed WHERE digest=?", (digest,)).fetchone():
            return "duplicate"
        if hae:
            return self._ingest_hae(p["data"], digest)
        tz = zone(p.get("timezone") or self.meta("tz") or DEFAULT_TZ)
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
                c.execute("INSERT OR REPLACE INTO samples(type, uuid, t, day, v, v2, src, v3) VALUES(?,?,?,?,?,?,?,?)",
                          (key, r.get("uuid") or f"{key}@{r['time']}", int(t.timestamp()), day(t),
                           r[f1], r.get(f2) if f2 else None, r.get("source"), pulse_of(r) if key == "blood_pressure" else None))
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
        n += self._glucose_hc(c, p, tz)   # blood glucose (#181)
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

    # ------------------------------------------------------------ Health Auto Export (iPhone, #148)
    def _ingest_hae(self, d, digest):
        c = self.db.cursor()
        n = 0
        for m in d.get("metrics") or []:
            if not isinstance(m, dict):
                continue
            name, rows = str(m.get("name") or "").lower(), [r for r in m.get("data") or [] if isinstance(r, dict)]
            if name == "sleep_analysis":
                n += sum(self._hae_sleep(c, r) for r in rows)
                continue
            if name == "blood_glucose":   # (#181)
                n += self._glucose_hae(c, m)
                continue
            if name in HAE_VITALS:   # home blood pressure and body temperature (#180, #184)
                n += self._hae_vitals(c, name, m.get("units"), rows)
                continue
            hk = HAE_QUANTITY.get(name)
            if not hk:
                continue
            key, kind = ah.QUANTITY[hk]
            unit = m.get("units")
            f = 1 if kind == "percent" else ah.UNITS[kind].get(unit, HAE_UNITS.get(kind, {}).get(unit))
            if f is None:
                log("skipped Health Auto Export's", name, "in a unit it can't convert:", repr(unit))
                continue
            for r in rows:
                t = hae_when(r.get("date"))
                raw = [r.get("qty"), None, None] if r.get("qty") is not None else [r.get("Avg"), r.get("Min"), r.get("Max")]
                try:
                    v, mn, mx = (float(x) * f if x is not None else None for x in raw)
                except (TypeError, ValueError):
                    continue
                if t is None or v is None:
                    continue
                if kind == "percent":   # HealthKit keeps a fraction (0.97); the app may send that or a percentage
                    v, mn, mx = (x * 100 if x is not None and x <= 1 else x for x in (v, mn, mx))
                c.execute("INSERT OR REPLACE INTO hae_points VALUES(?,?,?,?,?,?,?)",
                          (key, str(r.get("source") or ""), int(t.timestamp()), t.date().isoformat(), v, mn, mx))
                n += 1
        for w in d.get("workouts") or []:
            n += self._hae_workout(c, w) if isinstance(w, dict) else 0
        c.execute("INSERT INTO processed VALUES(?,?,?)", (0, digest, datetime.now(timezone.utc).isoformat()))
        self.meta("last_payload", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.db.commit()
        return f"{n} records"

    def _hae_sleep(self, c, r):
        src = str(r.get("source") or r.get("sleepSource") or "")
        if "startDate" in r:   # one segment ("Aggregate sleep data" off)
            s, e = hae_when(r.get("startDate")), hae_when(r.get("endDate"))
            stage = HAE_STAGE.get(str(r.get("value") or "").strip().lower(), "?")
            if s is None or e is None or e <= s or stage in (None, "?"):
                return 0
            c.execute("INSERT OR REPLACE INTO hae_sleep VALUES(?,?,?,?,NULL)", (src, s.isoformat(), e.isoformat(), stage))
            return 1
        # a whole night: hours per stage, without their order, laid end to end up to when sleep ended. A later
        # upload of the same night (it grew, or came again) replaces it.
        end = hae_when(r.get("sleepEnd")) or hae_when(r.get("inBedEnd"))
        if end is None:
            return 0
        try:
            h = {k: max(0.0, float(r.get(k) or 0)) for k in ("deep", "rem", "core", "asleep", "awake", "totalSleep")}
        except (TypeError, ValueError):
            return 0
        parts = [("deep", h["deep"]), ("rem", h["rem"]), ("light", h["core"]), ("sleeping", h["asleep"]), ("awake", h["awake"])]
        if not any(x for _, x in parts[:4]):
            parts[3] = ("sleeping", h["totalSleep"])
        night = f"{src}|{(hae_when(r.get('date')) or end).date().isoformat()}"
        c.execute("DELETE FROM hae_sleep WHERE night=?", (night,))
        t = end
        for stage, hours in reversed(parts):
            if hours:
                s = t - timedelta(hours=hours)
                c.execute("INSERT OR REPLACE INTO hae_sleep VALUES(?,?,?,?,?)", (src, s.isoformat(), t.isoformat(), stage, night))
                t = s
        return 1

    def _hae_workout(self, c, w):
        s, e = hae_when(w.get("start")), hae_when(w.get("end"))
        if s is None or e is None:
            return 0
        name = " ".join(str(w.get("name") or "Workout").split())
        words = name.lower().split()
        dur = w.get("duration")
        try:   # seconds, or {"qty", "units"}
            sec = float(dur["qty"]) * ah.DURATION.get(dur.get("units") or "s", 1) if isinstance(dur, dict) else float(dur)
        except (TypeError, ValueError, KeyError):
            sec = (e - s).total_seconds()
        key = ah.workout_key(s, e, ah.WORKOUT_PREFIX + name.replace(" ", ""))   # as an export.xml's same workout
        c.execute("INSERT OR REPLACE INTO exercise VALUES(?,?,?,?,?,?,?,?,?)",
                  (key, int(s.timestamp()), int(e.timestamp()), s.date().isoformat(), None, "_".join(words),
                   " ".join(words).capitalize(), round(sec), "health_auto_export"))
        return 1

    def _hae_summary(self):
        """{key: {day: value}} from Health Auto Export's uploads, made as the Apple Health importer makes an export's:
        cumulative metrics from the fullest source of the day, the rest averaged, sleep by night and best source."""
        agg = ah._Aggregates()
        for key, src, day, v, mn, mx in self.db.execute("SELECT key, src, day, v, mn, mx FROM hae_points"):
            if key in ah.CUMULATIVE:
                agg.cum[(key, day, src)] = agg.cum.get((key, day, src), 0.0) + v
                continue
            lo, hi = (v if mn is None else mn), (v if mx is None else mx)
            a = agg.avg.get((key, day))
            if a is None:
                agg.avg[(key, day)] = [v, 1, lo, hi]
            else:
                a[0] += v; a[1] += 1; a[2] = min(a[2], lo); a[3] = max(a[3], hi)
        for src, s, e, stage in self.db.execute("SELECT src, start, end_, stage FROM hae_sleep"):
            agg.sleep.append((src, datetime.fromisoformat(s), datetime.fromisoformat(e), stage))
        return {m["key"]: dict(m["pts"]) for m in agg.summary()["metrics"]}

    # ------------------------------------------------------------ blood glucose: meters and CGMs (#181, server/glucose.py)
    def _glucose_init(self):
        self.db.executescript(glucose.TABLE)
        if not self.loaded:
            self.meta("glucose", 1)   # a new database: every upload's glucose is folded in as it comes

    def _glucose_hc(self, c, p, tz):
        n, skipped = glucose.from_health_connect(c, p, tz)
        if skipped:
            log(f"left out {skipped} blood glucose entries that aren't single readings (hourly buckets, no value or time)")
        return n

    def _glucose_hae(self, c, m):
        n, skipped = glucose.from_hae(c, m, hae_when)
        if skipped:
            log(f"left out {skipped} of Health Auto Export's blood glucose rows: grouped into hours, or not in mg/dL or mmol/L")
        return n

    def _glucose_backfill(self):
        """A database from before the receiver read glucose: fold in the glucose of every upload in raw/ once (the
        uploads it already has are otherwise skipped as duplicates). Returns how many uploads had some."""
        if not self.loaded or self.meta("glucose") is not None:
            return 0
        c, n, tz = self.db.cursor(), 0, zone(self.meta("tz") or DEFAULT_TZ)
        for _, path in raw_files(self.slot):
            try:
                p = json.loads(read_raw(path))
                if is_hae(p):
                    got = sum(self._glucose_hae(c, m) for m in p["data"].get("metrics") or []
                              if isinstance(m, dict) and str(m.get("name") or "").lower() == "blood_glucose")
                elif isinstance(p, dict) and not p.get("test") and p.get("source") in ("health_connect", None):
                    got = self._glucose_hc(c, p, zone(p["timezone"]) if p.get("timezone") else tz)
                else:
                    got = 0
                n += bool(got)
            except Exception as e:
                log("skip", path, e)
        self.meta("glucose", 1)
        self.db.commit()
        if n:
            log(f"read the blood glucose of {n} earlier uploads")
        return n

    # ------------------------------------------------------------ home readings kept one by one (#180, #184)
    def _vitals_tables(self):
        """Health Auto Export's readings kept one by one, and the store's own migration: a database from before #180
        has samples without v3, a blood pressure reading's pulse (its readings there show, without one)."""
        if "v3" not in {r[1] for r in self.db.execute("PRAGMA table_info(samples)")}:
            self.db.execute("ALTER TABLE samples ADD COLUMN v3 REAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS hae_vitals(kind TEXT, src TEXT, t INTEGER, day TEXT, v REAL, v2 REAL,
            v3 REAL, PRIMARY KEY(kind, src, t))""")

    def _hae_vitals(self, c, name, unit, rows):
        """Health Auto Export's readings of a metric in HAE_VITALS, keyed by source and time (sent again: once); the day
        is the reading's own local date."""
        n = 0
        for r in rows:
            t = hae_when(r.get("date"))
            if t is None:
                continue
            row = None
            if name == "body_temperature":   # in °C, as Health Connect sends it
                v, scale = number(r.get("qty") if r.get("qty") is not None else r.get("Avg")), HAE_TEMP.get(str(unit or "").strip().lower())
                if v is not None and scale:
                    row = ("temp", round((v - 32) * 5 / 9, 2) if scale == "F" else v, None, None)
            elif name == "blood_pressure":
                sys_, dia = number(r.get("systolic")), number(r.get("diastolic"))
                if sys_ is not None and dia is not None:
                    row = ("bp", sys_, dia, pulse_of(r))
            if row:
                c.execute("INSERT OR REPLACE INTO hae_vitals VALUES(?,?,?,?,?,?,?)",
                          (row[0], str(r.get("source") or ""), int(t.timestamp()), t.date().isoformat(), *row[1:]))
                n += 1
        return n

    def _readings(self, kind):
        """[(t, day, v, v2, v3, source, app)] of one kind from both apps, oldest first. A reading with the same minute
        and values as one already listed (sent again under another id, or by both apps) is that one: Health Connect's
        comes first."""
        typ = {"bp": "blood_pressure", "temp": "body_temperature"}[kind]
        rows = [(t, d, number(v), number(v2), number(v3), src or "", "health_connect") for t, d, v, v2, v3, src in
                self.db.execute("SELECT t, day, v, v2, v3, src FROM samples WHERE type=? ORDER BY t, uuid", (typ,))]
        rows += [(t, d, v, v2, v3, src, "health_auto_export") for t, d, v, v2, v3, src in
                 self.db.execute("SELECT t, day, v, v2, v3, src FROM hae_vitals WHERE kind=? ORDER BY t, src", (kind,))]
        out, seen = [], set()
        for r in rows:
            k = (r[0] // 60, None if r[2] is None else round(r[2], 1), None if r[3] is None else round(r[3], 1))
            if r[2] is None or k in seen:
                continue
            seen.add(k)
            out.append(r)
        return sorted(out, key=lambda r: (r[0], r[6], r[5]))

    def _vitals_summary(self):
        """{"days": {metric key: {day: value}}, "bp_readings": [[time, systolic, diastolic, pulse, source, app]],
        "temp_readings": [[time, °C, source, app]]}: each day's readings, average and highest, and the readings
        themselves (time in UTC)."""
        days, out = {}, {}

        def put(key, d, v):
            days.setdefault(key, {})[d] = round(v, 2)
        bp = [r for r in self._readings("bp") if r[3] is not None]
        by = {}
        for _, d, s, di, *_ in bp:
            by.setdefault(d, []).append((s, di))
        for d, xs in by.items():
            put("bp_sys", d, sum(s for s, _ in xs) / len(xs))
            put("bp_dia", d, sum(di for _, di in xs) / len(xs))
            put("bp_sys_max", d, max(s for s, _ in xs))
            put("bp_dia_max", d, max(di for _, di in xs))
            put("bp_n", d, len(xs))
        if bp:
            out["bp_readings"] = [[datetime.fromtimestamp(t, timezone.utc).isoformat(), compact(s), compact(di),
                                   None if p is None else compact(p), src or None, APP[app]] for t, _, s, di, p, src, app in bp]
        temp, by = self._readings("temp"), {}
        for _, d, c, *_ in temp:
            by.setdefault(d, []).append(c)
        for d, xs in by.items():
            put("body_temp", d, sum(xs) / len(xs))
            put("body_temp_max", d, max(xs))
        if temp:
            out["temp_readings"] = [[datetime.fromtimestamp(t, timezone.utc).isoformat(), compact(c), src or None, APP[app]]
                                    for t, _, c, _, _, src, app in temp]
        return {"days": days, **out}

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
        rmssd = "hrv" in out   # Health Connect's HRV; Apple's (SDNN) measures something else, so not on its line
        for k, days in self._hae_summary().items():
            k = "hrv_sdnn" if k == "hrv" and rmssd else k
            for d, v in days.items():
                out.setdefault(k, {}).setdefault(d, v)   # a day Health Connect gave this metric for keeps its value
        exercises =[{"start": datetime.fromtimestamp(s, timezone.utc).isoformat(), "end": datetime.fromtimestamp(e, timezone.utc).isoformat(),
                      "name": (title or name or f"type {typ}"), "kind": name or typ, "minutes": round((dur or (e - s)) / 60)}
                     for s, e, typ, name, title, dur in q("SELECT start_t, end_t, type, name, title, duration FROM exercise ORDER BY start_t")]
        META = {
            "steps": ("Steps", "steps", "Activity"), "distance_km": ("Distance", "km", "Activity"),
            "active_cal": ("Active calories", "kcal", "Activity"),
            "hr_rest": ("Resting heart rate", "bpm", "Heart"), "hr_avg": ("Heart rate, daily average", "bpm", "Heart"),
            "hr_min": ("Heart rate, daily low", "bpm", "Heart"), "hr_max": ("Heart rate, daily high", "bpm", "Heart"),
            "hrv": ("HRV (RMSSD)" if rmssd else "HRV (SDNN)", "ms", "Heart"), "hrv_sdnn": ("HRV (SDNN)", "ms", "Heart"),
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
        vitals = self._vitals_summary()   # home blood pressure and body temperature (#180, #184)
        META.update(VITALS_META)
        out.update(vitals.pop("days"))
        metrics = [{"key": k, "name": META[k][0], "unit": META[k][1], "group": META[k][2],
                    "pts": sorted(out[k].items())} for k in META if k in out]
        metrics += glucose.summary(self.db)   # blood glucose (#181): meter readings, and CGM days summarised
        return {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tz": self.meta("tz") or DEFAULT_TZ,
                "last_payload": self.meta("last_payload"), "metrics": metrics, "exercise": exercises, **vitals}

    def write_summary(self):
        path = summary_file(self.slot)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        crypto.write_file(path, json.dumps(self.summary(), separators=(",", ":")).encode())


def is_hae(p):
    """A Health Auto Export upload: {"data": {"metrics": [...], "workouts": [...]}}."""
    d = p.get("data") if isinstance(p, dict) else None
    return isinstance(d, dict) and ("metrics" in d or "workouts" in d)


def hae_when(s):
    """Health Auto Export's dates, "2024-03-09 08:00:00 -0500" (or on a 12-hour clock, "8:00:00 AM -0500", maybe with
    a narrow space) -> an aware datetime in that offset, whose local date is the reading's day."""
    if not isinstance(s, str):
        return None
    s = " ".join(s.replace(" ", " ").split())
    if (t := ah._when(s)) is not None:
        return t
    for fmt in ("%Y-%m-%d %I:%M:%S %p %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return t if t.tzinfo else None
    except ValueError:
        return None


def archive(body, slot=""):
    now = datetime.now(timezone.utc)
    d = os.path.join(slot_dir(slot), "raw", now.strftime("%Y/%m/%d"))
    os.makedirs(d, exist_ok=True)
    digest = hashlib.sha256(body).hexdigest()
    path = os.path.join(d, f"{now.strftime('%H%M%S%f')}-{digest[:12]}.json.gz")
    crypto.write_file(path, gzip.compress(body))   # still named .json.gz: gzipped, then sealed
    return digest


work = queue.Queue()


def worker(stores):
    """Folds uploads in, one at a time; a slot's snapshot and summary are written once uploads stop coming."""
    dirty = set()
    while True:
        try:
            item = work.get(timeout=5)
        except queue.Empty:
            for slot in sorted(dirty):
                try:
                    stores[slot].save(); stores[slot].write_summary(); log("summary written:", slot or "first profile")
                except Exception:
                    traceback.print_exc()
            dirty = set()
            continue
        slot, body, digest = item
        try:
            if slot not in stores:
                stores[slot] = Store(slot)
            res = stores[slot].ingest(json.loads(body), digest)
            log("ingested", res, "for", slot or "first profile")
            if res not in ("skipped", "duplicate"):
                dirty.add(slot)
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
        given, slot = (self.headers.get("X-Signature") or "").encode(), None
        auth = (self.headers.get("Authorization") or "").strip()   # Health Auto Export's header: "Bearer <secret>"
        token = (auth[7:].strip() if auth[:7].lower() == "bearer " else "").encode()
        for s, key in keys():   # every one compared, so the time taken doesn't say which matched
            expected = ("sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()).encode()
            signed = hmac.compare_digest(expected, given)
            bearer = hmac.compare_digest(key, token) if token else False
            if (signed or bearer) and slot is None:
                slot = s
        if slot is None:
            log("rejected: bad signature from", self.client_address[0])
            return self._send(401)
        try:
            json.loads(body)
        except ValueError:
            return self._send(400)
        digest = archive(body, slot)
        work.put((slot, body, digest))
        self._send(200, b"ok")

    def log_message(self, *a):
        pass


def reprocess():
    for slot in slots_on_disk():
        db = os.path.join(slot_dir(slot), "watch.db")
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(db + ext):
                os.remove(db + ext)
        store = Store(slot)
        n = store.catch_up()   # a new database: every upload in raw/
        store.save()
        store.write_summary()
        log(f"reprocessed {len(raw_files(slot))} payloads of {slot or 'the first profile'} ({n} with new records)")


def load_key():
    try:
        crypto.keyring.load()
    except crypto.KeyError_ as e:
        sys.exit(f"{e} The setup service makes it (docker compose up); the receiver uses the app's key.")


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    load_key()
    if "--reprocess" in sys.argv:
        reprocess(); sys.exit(0)
    if secret() is None and not os.path.isfile(KEYS_FILE):   # fail fast when there's no secret at all
        sys.exit(f"No secret: neither {SECRET_FILE} nor {KEYS_FILE} exists.")
    if (n := migrate()):
        log(f"encrypted {n} files with the current key")
    stores = {}
    for slot in slots_on_disk():
        store = stores[slot] = Store(slot)
        if (n := store.catch_up()):
            log(f"folded in {n} uploads of {slot or 'the first profile'} archived since the last snapshot")
            store.save(); store.write_summary()
        elif not os.path.exists(summary_file(slot)):
            store.write_summary()
    log(f"taking uploads signed with {len(keys())} secret(s)")
    threading.Thread(target=worker, args=(stores,), daemon=True).start()
    log(f"listening on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
