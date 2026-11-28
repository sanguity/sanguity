#!/usr/bin/env python3
"""Receiver for the phone apps' uploads, on one address: Life Dashboard (Health Connect, Android) webhooks,
and Health Auto Export (Apple Health, iPhone) REST API automations (#148).

- Verifies X-Signature (HMAC-SHA256 of the raw body; Life Dashboard), or the secret itself sent as
  "Authorization: Bearer <secret>" (Health Auto Export, which can add a header but not sign), with one profile's
  secret (below), which says whose upload it is. An upload with neither header, or a wrong bearer secret, is refused
  before its body is read; an address that sent BAD_FREE bad signatures or secrets in 15 minutes waits before the
  receiver reads another signed body from it, the waits doubling up to 15 minutes, as for sign-ins (#222).
- Archives every payload, gzipped, under <slot>/raw/YYYY/MM/DD/ (the source of truth).
- Folds records into an SQLite database following docs/webhook.md:
  dedupe on uuid / sequence, bucket replace-vs-combine, deletions.
- Health Auto Export's JSON ({"data": {"metrics": [...], "workouts": [...]}}) is kept reading by reading, keyed by
  metric, source and time (a whole sleep night by source and day, a workout by start, end and type), so repeated and
  overlapping uploads count once; days are each reading's own local date. The day's figures are made with the Apple
  Health importer's own mapping and aggregation (server/importers/apple_health.py), so an iPhone gives the same
  Health tab as an uploaded export.xml, HRV as SDNN. On a day both apps sent one metric, Health Connect's value wins.
- Blood glucose from both apps (#181, server/glucose.py): every reading kept, meter readings passed on as they are,
  a continuous glucose monitor's summarised per day (average, low, high, hours with readings, time in ranges, and
  each hour of the day's count and percentiles for the 24-hour profile, #303).
- Home blood pressure (#180) and body temperature (#184) are kept reading by reading from both apps, a reading sent
  again (or by both) counted once, and summed up by day; the summary lists the readings too.
- ECG recordings and irregular rhythm notifications from Health Auto Export (#182, server/ecg.py): each one kept by its
  key (sent again: once), listed in the summary's "ecg"; a recording's samples written beside the summary as a file of
  its own, ecg/<16 hex>.json.gz, sealed, which the app reads when its drawer opens. Health Connect has no ECG type.
- Medication doses from Health Auto Export (#235, server/med_log.py): Apple Health's Medications dose events (iOS 26
  and later, the app's "medications" list), each kept by its medication, time and scheduled time (sent again: once),
  only while marked Taken or Skipped (another status, such as Not Logged, takes it out again), listed in the summary's
  "med_doses". Health Connect has no dose events.
- A workout's heart rate (#197, server/hrzones.py): Health Auto Export's readings during it (or only its average and
  maximum), Health Connect's heart rate samples in the session's time; kept as seconds at each bpm, so the app works
  out the zones for whatever maximum heart rate applies.
- Writes per-day summaries for Sanguity: $DATA/public/daily.json, or public/profiles/<slot>/daily.json.
- Menstrual cycle records (#183, server/cycle.py) only for a profile whose owner turned cycle tracking on ("cycle" in
  $KEYS_FILE): for any other they are taken out of the upload before it is archived, so they are never kept at all.
  Sexual activity is taken out of every upload. A profile's cycle records are in public/.../cycle.json, never in
  daily.json; turning tracking off, or asking for them to be deleted, deletes them here and in every archived upload.
- Backups (#227, server/backup.py): the app reads this folder read-only and takes each watch.db and raw/ into its
  backups. Restoring one leaves them in keys/restore/ (the one folder here the app writes); on start, before anything
  else, apply_restore() moves what is here to $DATA/before-restore-<time>/ and the backup's in. That folder is
  deleted $RESTORE_KEEP_DAYS (30) days after the restore (restore_sweep, #593).

One secret per profile (#46). The app makes each profile's secret on its Settings page and writes them all, sealed,
to $KEYS_FILE (server/watch_keys.py); an upload goes to the profile whose secret signed it. Each profile has a slot:
"" is the receiver's top folder ($DATA/watch.db, $DATA/raw/, public/daily.json), as before #46; any other is the
profile's id, with its own $DATA/profiles/<slot>/watch.db and raw/, and public/profiles/<slot>/daily.json. The
install's single secret from before, $SECRET_FILE, still works for slot "" until that profile (the first) makes one
of its own in the app ("legacy": false in $KEYS_FILE), and always while there's no $KEYS_FILE. A profile deleted in
the app is listed under "deleted" in $KEYS_FILE, and everything its slot keeps is deleted here (slot_sweep, #588);
its secret is gone from the file already, and its id is never a profile's again. An upload whose secret goes while
it's being kept is taken out again and refused. A slot no profile reads ("slots" in $KEYS_FILE) and no secret uploads
to (one a profile deleted before #588 left) is deleted $SLOT_GRACE_DAYS (7) days after it's first found so
(orphan_sweep, #593), at start and hourly after; nothing is taken while $KEYS_FILE doesn't say which slots are read.

Encrypted at rest with the app's key ($SANGUITY_KEY_FILE, see server/crypto.py): every file here is sealed, so
it runs on the app's image and imports server.crypto. The database is kept in memory and saved as a sealed
snapshot, $DATA/watch.db, once uploads have been folded in; on start it is loaded back and the uploads archived
since the last snapshot are folded in again (a crash loses nothing raw/ holds). Plaintext files from before
encryption, and files sealed with an older key, are re-sealed with the current key on start.

Run:  python3 receiver.py            (serve on :8080)
      python3 receiver.py --reprocess (rebuild watch.db and daily.json from raw/; with the receiver stopped)
"""
import gzip, hashlib, hmac, json, os, queue, re, shutil, sqlite3, sys, threading, time, traceback
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
from server import cycle   # noqa: E402  (menstrual cycle tracking, opt-in, #183)
from server import ecg   # noqa: E402  (ECG recordings and irregular rhythm notifications, #182)
from server import hrzones   # noqa: E402  (a workout's heart rate, #197)
from server import security   # noqa: E402  (the app's own throttle, for bad signatures, #222)

DATA = os.environ.get("DATA", "/data")
SECRET_FILE = os.environ.get("SECRET_FILE", "/run/secret/watch_secret")
KEYS_FILE = os.environ.get("KEYS_FILE") or os.path.join(DATA, "keys", "receiver-secrets.json")
# (#1121) the receiver's settings changed on the app's Settings → General (Needs a restart): the app copies them here, beside
# the secrets, at Restart now (server/server_settings.py). Read once, as the receiver starts; settings_watch restarts it
# when the file changes. The environment's (TZ_DEFAULT, SLOT_GRACE_DAYS, RESTORE_KEEP_DAYS) still wins where set.
SETTINGS_FILE = os.path.join(os.path.dirname(KEYS_FILE), "receiver-settings.json")


def _settings_sig():
    try:
        st = os.stat(SETTINGS_FILE)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _read_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


SETTINGS_SIG = _settings_sig()   # (before the read: a change while reading is a change seen)
GIVEN = _read_settings()


def _zone_given():
    """The time zone the app copied here, where this machine's time zone database knows it; else None."""
    name = GIVEN.get("TZ")
    if not isinstance(name, str) or not name:
        return None
    try:
        ZoneInfo(name)
        return name
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC" if name == "UTC" else None


DEFAULT_TZ = os.environ.get("TZ_DEFAULT", "").strip() or _zone_given() or "UTC"
PORT = int(os.environ.get("PORT", "8080"))
MAX_BODY = 32 * 1024 * 1024
BAD_FREE = 10   # bad signatures or secrets an address may send in 15 minutes before it waits (#222)


def _days(name, default):
    """A number of days from the environment, else the app's copy (#1121), in seconds; the default when neither is set,
    or it's unreadable or below zero."""
    try:
        v = float(os.environ.get(name) or GIVEN.get(name) or default)
    except (TypeError, ValueError):
        v = default
    return (v if 0 <= v < 1e6 else default) * 86400   # (NaN isn't >= 0)


SLOT_GRACE = _days("SLOT_GRACE_DAYS", 7)   # a slot nothing names is deleted this long after it's first found (#593)
RESTORE_KEEP = _days("RESTORE_KEEP_DAYS", 30)   # a before-restore-<time>/ folder is deleted this long after (#593)


class BadSignatures(security.Throttle):
    """Bad signatures and secrets per address (#222), in memory: after BAD_FREE inside the window, the address waits
    (30 seconds, doubling, at most 15 minutes) before another signed body is read. A restart forgets them. A phone
    still signing with the secret from before one was made again in the app is refused either way; given the new one,
    it waits at most 15 minutes. A right bearer secret is known before the body is read, so it's never held up."""
    FREE = {"ip": BAD_FREE}


throttle = BadSignatures()

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
    # cardio fitness (#226): Health Connect's Vo2MaxRecord, its value field named after the record's property
    # (vo2MillilitersPerMinuteKilogram) as Life Dashboard names the others ("kilocalories_per_day"); inferred, not seen
    "vo2_max": ("vo2_milliliters_per_minute_kilogram", None),
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
    "vo2_max": "HKQuantityTypeIdentifierVO2Max",   # cardio fitness (#226)
}
HAE_UNITS = {"rate": {"bpm": 1, "breaths/min": 1}, "count": {"steps": 1},   # spellings besides HealthKit's own
             "vo2": {"ml/(kg·min)": 1, "ml/(kg*min)": 1, "ml/kg/min": 1, "ml/kg·min": 1, "mL/(kg·min)": 1, "mL/kg/min": 1}}
HAE_STAGE = {   # a sleep segment's "value" -> the receiver's stage; in bed overlaps the others and isn't sleep
    "core": "light", "deep": "deep", "rem": "rem", "awake": "awake", "asleep": "sleeping", "unspecified": "sleeping",
    "in bed": None, "inbed": None}

# ---- home readings kept one by one (#180, #184): blood pressure and body temperature, from both phone apps
HAE_VITALS = {"blood_pressure", "body_temperature"}   # Health Auto Export metrics kept reading by reading (Store._hae_vitals)
VITALS_META = ah.VITALS_META   # the daily summary of them, after the other metrics (shared with export.xml, #223)
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


def unit_shown(unit):
    """A unit the phone sent, for the log only if it looks like one (#592): short, letters, digits and unit marks; any
    other text from the payload isn't written."""
    return repr(unit) if isinstance(unit, str) and re.fullmatch(r"[A-Za-z0-9/%.·°µ^ _-]{1,16}", unit) else "(not shown)"


def quiet(e):
    """An exception as the log says it (#592): its type and the line it was raised at, never its message nor a
    traceback's, as the app's QuietTracebacks: a payload that doesn't parse can be quoted in both (a reading's value,
    a medication's name)."""
    tb = traceback.extract_tb(e.__traceback__) if e is not None else []
    where = f" at {os.path.basename(tb[-1].filename)}:{tb[-1].lineno} in {tb[-1].name}" if tb else ""
    return f"{type(e).__name__}{where} (its message isn't logged: it may quote health data)"


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
    paths = [p for _, p in raw_files(slot)] + [db, summary_file(slot)] + ecg_files(slot)   # ECG samples (#182)
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
        self.db.executescript(cycle.TABLE)   # menstrual cycle records, kept only while tracking is on (#183)
        self._ecg_init()   # ECG recordings and irregular rhythm notifications (#182)
        self._workout_hr_init()   # a workout's heart rate (#197)
        self._meds_init()   # medication doses (#235)

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
        n += self._glucose_hourly_backfill()   # and its CGM days' hours, once (#303)
        n += self._ecg_backfill()   # and its ECGs, once (#182)
        n += self._workout_hr_backfill()   # and its Health Auto Export workouts' heart rate, once (#197)
        n += self._meds_backfill()   # and its medication doses, once (#235)
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
                log("skip", p, quiet(e))
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
            c.execute(EXERCISE_INSERT,
                      (r.get("uuid") or f"ex@{r['start_time']}", int(s.timestamp()), int(e.timestamp()), day(s),
                       r.get("type"), r.get("name"), r.get("title"), r.get("duration_seconds"), r.get("source"), None))
            n += 1
        n += self._glucose_hc(c, p, tz)   # blood glucose (#181)
        n += self._cycle_in(lambda: cycle.from_health_connect(c, p, tz)) if cycle.has_any(p) else 0   # (#183)
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
            if cycle.hae_name(name) in cycle.HAE:   # menstrual cycle records, only while tracking is on (#183)
                n += self._cycle_in(lambda: cycle.from_hae(c, m, hae_when))
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
                log("skipped Health Auto Export's", name, "in a unit it can't convert:", unit_shown(unit))
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
        n += self._ecg_hae(c, d)   # (#182)
        n += self._meds_hae(c, d)   # (#235)
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
        hr = hae_workout_hr(w, s, e)   # (#197)
        c.execute(EXERCISE_INSERT,
                  (key, int(s.timestamp()), int(e.timestamp()), s.date().isoformat(), None, "_".join(words),
                   " ".join(words).capitalize(), round(sec), "health_auto_export", json.dumps(hr) if hr else None))
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
            self.meta("glucose_hourly", 1)   # and its summaries have each CGM day's hours from the first (#303)

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
                log("skip", path, quiet(e))
        self.meta("glucose", 1)
        self.db.commit()
        if n:
            log(f"read the blood glucose of {n} earlier uploads")
        return n

    def _glucose_hourly_backfill(self):
        """A database from before the summary kept each CGM day's hours (#303): their readings are here already, so its
        summary is written again once, on start, for the days it has to get them (catch_up's count says so). Returns 1
        then, else 0."""
        if not self.loaded or self.meta("glucose_hourly") is not None:
            return 0
        self.meta("glucose_hourly", 1)
        self.db.commit()
        if self.db.execute("SELECT 1 FROM glucose LIMIT 1").fetchone() is None:
            return 0
        log("each CGM day's hours of readings: the summary is written again with them")
        return 1

    # ------------------------------------------------------------ ECGs and irregular rhythm notifications (#182, server/ecg.py)
    def _ecg_init(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS ecg(key TEXT PRIMARY KEY, start TEXT, meta TEXT, wave BLOB)")
        if not self.loaded:
            self.meta("ecg", 1)   # a new database: every upload's ECGs are folded in as they come

    def _ecg_hae(self, c, d):
        """Health Auto Export's ECGs and irregular rhythm notifications, by key: one sent again replaces itself, keeping
        what only the earlier copy had (its samples, when the later one came without)."""
        evs, waves, skipped = ecg.from_hae(d, hae_when)
        if skipped:
            log(f"left out {skipped} of Health Auto Export's ECG and heart notification entries (no start it could read, "
                "or a high or low heart rate alert)")
        for ev in evs:
            old = c.execute("SELECT meta, wave FROM ecg WHERE key=?", (ev["key"],)).fetchone()
            meta = {**json.loads(old[0]), **ev} if old else ev
            wave = ecg.pack(waves[ev["key"]]) if ev["key"] in waves else (old[1] if old else None)
            c.execute("INSERT OR REPLACE INTO ecg VALUES(?,?,?,?)", (ev["key"], ev["start"], json.dumps(meta), wave))
        return len(evs)

    def _ecg_backfill(self):
        """A database from before the receiver read ECGs: fold in those of every Health Auto Export upload in raw/ once
        (an ECG automation's uploads were archived, then left out). Returns how many uploads had some."""
        if not self.loaded or self.meta("ecg") is not None:
            return 0
        c, n = self.db.cursor(), 0
        for _, path in raw_files(self.slot):
            try:
                p = json.loads(read_raw(path))
                n += bool(is_hae(p) and self._ecg_hae(c, p["data"]))
            except Exception as e:
                log("skip", path, quiet(e))
        self.meta("ecg", 1)
        self.db.commit()
        if n:
            log(f"read the ECGs of {n} earlier uploads")
        return n

    def _ecg_summary(self):
        return [{**json.loads(m), **({"wave": True} if w else {})}
                for m, w in self.db.execute("SELECT meta, wave IS NOT NULL FROM ecg ORDER BY start, key")]

    def write_waves(self):
        """Each recording's samples beside the summary (ecg/<16 hex>.json.gz, sealed), and none it no longer lists."""
        d = os.path.join(os.path.dirname(summary_file(self.slot)), "ecg")
        keep = set()
        for k, w in self.db.execute("SELECT key, wave FROM ecg WHERE wave IS NOT NULL"):
            name = ecg.file_name(k)
            if not name:
                continue
            keep.add(name)
            if not os.path.isfile(os.path.join(d, name)):
                os.makedirs(d, exist_ok=True)
                crypto.write_file(os.path.join(d, name), w)
        for name in os.listdir(d) if os.path.isdir(d) else ():
            if name.endswith(".json.gz") and name not in keep:
                os.remove(os.path.join(d, name))

    # ------------------------------------------------------------ medication doses from Health Auto Export (#235)
    def _meds_init(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS hae_doses(key TEXT PRIMARY KEY, start INTEGER, row TEXT)")
        if not self.loaded:
            self.meta("meds", 1)   # a new database: every upload's doses are folded in as they come

    def _meds_hae(self, c, d):
        """Health Auto Export's dose events, by key: one sent again replaces itself, and one no longer marked Taken or
        Skipped (the person un-logged it) is taken out."""
        n = 0
        for m in d.get("medications") or []:
            got = hae_dose(m) if isinstance(m, dict) else None
            if got is None:
                continue
            key, start, row = got
            if row is None:
                c.execute("DELETE FROM hae_doses WHERE key=?", (key,))
            else:
                c.execute("INSERT OR REPLACE INTO hae_doses VALUES(?,?,?)", (key, start, json.dumps(row)))
                n += 1
        return n

    def _meds_backfill(self):
        """A database from before the receiver read doses: fold in those of every Health Auto Export upload in raw/
        once. Returns how many uploads had some."""
        if not self.loaded or self.meta("meds") is not None:
            return 0
        c, n = self.db.cursor(), 0
        for _, path in raw_files(self.slot):
            try:
                p = json.loads(read_raw(path))
                n += bool(is_hae(p) and self._meds_hae(c, p["data"]))
            except Exception as e:
                log("skip", path, quiet(e))
        self.meta("meds", 1)
        self.db.commit()
        if n:
            log(f"read the medication doses of {n} earlier uploads")
        return n

    def _meds_summary(self):
        return [json.loads(r) for (r,) in self.db.execute("SELECT row FROM hae_doses ORDER BY start, key")]

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
        return ah.once(rows)   # the same rule as an uploaded export.xml's (#223)

    def _vitals_summary(self):
        """{"days": {metric key: {day: value}}, "bp_readings": [[time, systolic, diastolic, pulse, source, app]],
        "temp_readings": [[time, °C, source, app]]}: each day's readings, average and highest, and the readings
        themselves (time in UTC). Summed up as an uploaded export.xml's are (apple_health.home_summary, #223)."""
        name = lambda rows: [(*r[:6], APP[r[6]]) for r in rows]
        return ah.home_summary(name(self._readings("bp")), name(self._readings("temp")))

    # ------------------------------------------------------------ menstrual cycle tracking, opt-in (#183, server/cycle.py)
    def _cycle_in(self, fold):
        """Fold an upload's cycle records in, only while this profile has tracking on (an upload archived before it
        was turned off, read again, keeps nothing)."""
        if not cycle_on(self.slot):
            return 0
        self.meta("cycle_clean", 0)   # there's something to delete when it's turned off
        return fold()

    def cycle_purge(self, token=None):
        """Delete every cycle record this profile's slot keeps: the database's (vacuumed, so nothing is left in its
        free pages) and each archived upload's, rewritten without them. token: the deletion the app asked for, said
        back in cycle.json. Returns how many archived uploads were rewritten."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.db.execute("DELETE FROM cycle")
        n = 0
        for _, path in raw_files(self.slot):
            try:
                body = read_raw(path)
                kept = cycle.keep(body, False)
            except Exception as e:   # unreadable: it was never folded in either
                log("skip", path, type(e).__name__)
                continue
            if kept is not body:
                crypto.write_file(path, gzip.compress(kept))
                self.db.execute("INSERT OR IGNORE INTO processed VALUES(?,?,?)", (0, hashlib.sha256(kept).hexdigest(), now))
                n += 1
        self.meta("cycle_clean", 1)
        if token:
            self.meta("cycle_purged", token)
            self.meta("cycle_purged_at", now)
        self.db.commit()
        self.db.execute("VACUUM")
        self.save()
        self.write_summary()
        log(f"deleted the cycle records of {self.slot or 'the first profile'} ({n} archived uploads rewritten)")
        return n

    def _cycle_write(self):
        """cycle.json: while tracking is on, every record kept; after a deletion, which one was done (the app shows
        it); otherwise no file."""
        path, on, token = cycle_file(self.slot), cycle_on(self.slot), self.meta("cycle_purged")
        if on or token:
            doc = {"on": on, "purged": token, "purged_at": self.meta("cycle_purged_at"),
                   "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "records": cycle.records(self.db) if on else []}
            os.makedirs(os.path.dirname(path), exist_ok=True)
            crypto.write_file(path, json.dumps(doc, separators=(",", ":")).encode())
        elif os.path.exists(path):
            os.remove(path)

    # ------------------------------------------------------------ a workout's heart rate (#197, server/hrzones.py)
    def _workout_hr_init(self):
        """The store's own migration: exercise gains hr (Health Auto Export's heart rate for the workout, as
        hrzones.packed writes it), and samples an index by time, for the heart rate during a Health Connect session.
        A database from before reads its Health Auto Export workouts again from raw/ once (_workout_hr_backfill)."""
        if "hr" not in {r[1] for r in self.db.execute("PRAGMA table_info(exercise)")}:
            self.db.execute("ALTER TABLE exercise ADD COLUMN hr TEXT")
        self.db.execute("CREATE INDEX IF NOT EXISTS samples_t ON samples(type, t)")
        if not self.loaded:
            self.meta("workout_hr", 1)

    def _workout_hr_backfill(self):
        if not self.loaded or self.meta("workout_hr") is not None:
            return 0
        c, n = self.db.cursor(), 0
        for _, path in raw_files(self.slot):
            try:
                p = json.loads(read_raw(path))
                if is_hae(p):
                    n += sum(self._hae_workout(c, w) for w in p["data"].get("workouts") or [] if isinstance(w, dict))
            except Exception as e:
                log("skip", path, quiet(e))
        self.meta("workout_hr", 1)
        self.db.commit()
        return 0

    def _workout_hr(self, s, e, hr):
        """A workout's "hr" for the summary: Health Auto Export's as kept, else the heart rate samples Health Connect
        sent during it (hourly buckets can't be put in zones); None without either."""
        if hr:
            return json.loads(hr)
        got = hrzones.packed(*hrzones.histogram(self.db.execute(
            "SELECT t, v FROM samples WHERE type='heart_rate' AND t BETWEEN ? AND ?", (s, e)).fetchall(), s, e))
        return got or None

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
                                      ("vo2_max", "vo2max", None, None),   # cardio fitness (#226)
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
                      "name": (title or name or f"type {typ}"), "kind": name or typ, "minutes": round((dur or (e - s)) / 60),
                      **({"hr": h} if (h := self._workout_hr(s, e, hr)) else {})}   # its heart rate (#197)
                     for s, e, typ, name, title, dur, hr in q("SELECT start_t, end_t, type, name, title, duration, hr FROM exercise ORDER BY start_t")]
        META = {
            "steps": ("Steps", "steps", "Activity"), "distance_km": ("Distance", "km", "Activity"),
            "active_cal": ("Active calories", "kcal", "Activity"),
            "hr_rest": ("Resting heart rate", "bpm", "Heart"), "hr_avg": ("Heart rate, daily average", "bpm", "Heart"),
            "hr_min": ("Heart rate, daily low", "bpm", "Heart"), "hr_max": ("Heart rate, daily high", "bpm", "Heart"),
            "hrv": ("HRV (RMSSD)" if rmssd else "HRV (SDNN)", "ms", "Heart"), "hrv_sdnn": ("HRV (SDNN)", "ms", "Heart"),
            "spo2_avg": ("SpO2, daily average", "%", "Oxygen & breathing"), "spo2_min": ("SpO2, daily low", "%", "Oxygen & breathing"),
            "resp": ("Respiratory rate", "breaths/min", "Oxygen & breathing"),
            "vo2max": ah.META["vo2max"],   # cardio fitness (#226)
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
        ecgs = self._ecg_summary()   # ECGs and irregular rhythm notifications (#182), without their samples
        doses = self._meds_summary()   # medication doses logged in Apple Health (#235)
        return {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tz": self.meta("tz") or DEFAULT_TZ,
                "last_payload": self.meta("last_payload"), "metrics": metrics, "exercise": exercises, **vitals,
                **({"ecg": ecgs} if ecgs else {}), **({"med_doses": doses} if doses else {})}

    def write_summary(self):
        path = summary_file(self.slot)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.write_waves()   # first, so the summary never lists samples that aren't there yet (#182)
        crypto.write_file(path, json.dumps(self.summary(), separators=(",", ":")).encode())
        self._cycle_write()   # (#183)


# ---- menstrual cycle tracking, opt-in (#183): which profiles turned it on, and the deletions they asked for
def cycle_state():
    """({slots with cycle tracking on}, {slot: the latest deletion asked for}) from $KEYS_FILE, or None while it
    exists but couldn't be read yet: then nothing is kept, and nothing is deleted."""
    keys()   # reads the file again when it changed
    doc = _keys["doc"]
    if not isinstance(doc, dict):
        return (set(), {}) if _keys["sig"] is None else None   # no file: no profile has turned it on
    on = {s for s in doc.get("cycle") or [] if isinstance(s, str)}
    purge = doc.get("cycle_purge")
    return on, {str(k): str(v) for k, v in purge.items() if v} if isinstance(purge, dict) else {}


def cycle_on(slot):
    st = cycle_state()
    return st is not None and slot in st[0]


def cycle_file(slot):
    return os.path.join(os.path.dirname(summary_file(slot)), "cycle.json")


def cycle_sweep(stores):
    """Carry out what the app asked: a profile that turned tracking off keeps nothing (its records and every archived
    upload's go, once), and a deletion asked for is done once, and said back in cycle.json. Once the app no longer asks
    for it (it has seen it done, and forgotten it: cycle_tracking.forget_done, #935), what says it was done goes too
    (#946): the store's note of it, and cycle.json where tracking is off, so nothing here says it was ever on."""
    st = cycle_state()
    if st is None:
        return
    on, purge = st
    for slot in sorted(set(stores) | set(purge)):
        store, token = stores.get(slot), purge.get(slot)
        if store is not None:
            if token and token != store.meta("cycle_purged"):
                store.cycle_purge(token)
            elif slot not in on and store.meta("cycle_clean") != "1":
                store.cycle_purge()
            elif not token and store.meta("cycle_purged") is not None:   # (#946) seen done by the app
                store.db.execute("DELETE FROM meta WHERE k IN ('cycle_purged', 'cycle_purged_at')")
                store.save()
                store._cycle_write()
        elif token:   # nothing ever received for it: nothing to delete, but say it's done
            path = cycle_file(slot)
            try:
                done = os.path.isfile(path) and json.loads(crypto.read_file(path)).get("purged") == token
            except Exception:
                done = False
            if not done:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                crypto.write_file(path, json.dumps({"on": slot in on, "purged": token, "records": [],
                                                    "purged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}).encode())
    # (#946) a deletion said done for a slot that never received anything (just above), no longer asked for: it goes
    top = os.path.join(DATA, "public", "profiles")
    for slot in [""] + sorted(n for n in (os.listdir(top) if os.path.isdir(top) else ()) if n.isdigit()):
        path = cycle_file(slot)
        if slot in stores or slot in purge or slot in on or os.path.isfile(os.path.join(slot_dir(slot), "watch.db")) or not os.path.isfile(path):
            continue
        try:
            doc = json.loads(crypto.read_file(path))
        except Exception:
            continue
        if isinstance(doc, dict) and doc.get("purged") and not doc.get("records"):
            os.remove(path)


DELETED_DONE = "deleted-slots.json"   # in $DATA: the deletion of slot "" done last (#588); a profile slot: its absence


def deleted_state():
    """{slot: the deletion asked for} from $KEYS_FILE: the slots of profiles deleted in the app (#588). None while it
    exists but couldn't be read yet: then nothing is deleted."""
    keys()   # reads the file again when it changed
    doc = _keys["doc"]
    if not isinstance(doc, dict):
        return {} if _keys["sig"] is None else None
    asked = doc.get("deleted")
    if not isinstance(asked, dict):
        return {}
    return {str(k): str(v) for k, v in asked.items() if v and (k == "" or str(k).isdigit())}


def slot_paths(slot):
    """Everything kept for a slot: its database and archived uploads, and its summary, ECG samples and cycle.json
    (what the app reads). A profile slot's are two folders; slot "", the top folder's own files (never keys/,
    profiles/, the other slots' public/profiles/ or a restore's before-restore-*/)."""
    if slot != "":
        return [slot_dir(slot), os.path.dirname(summary_file(slot))]
    pub = os.path.dirname(summary_file(""))
    return [os.path.join(DATA, n) for n in ("watch.db", "watch.db-wal", "watch.db-shm", "raw")] + \
           [summary_file(""), os.path.join(pub, "ecg"), cycle_file("")]


def _remove(path):
    """Moved aside first (a name slots_on_disk skips), then deleted, so a failure halfway leaves no slot half there."""
    if not os.path.lexists(path):
        return
    aside = os.path.join(os.path.dirname(path), ".deleting-" + os.path.basename(path))
    shutil.rmtree(aside, ignore_errors=True)
    os.replace(path, aside)
    if os.path.isdir(aside) and not os.path.islink(aside):
        shutil.rmtree(aside)
    else:
        os.remove(aside)


def live_slots():
    """The slots a secret uploads to now."""
    return {slot for slot, _ in keys()}


def named_slots():
    """Every slot a profile in the app reads ("slots" in $KEYS_FILE, #593) or a secret uploads to. None when the file
    doesn't say (there's none, it couldn't be read yet, or an app from before #593 wrote it): then no slot is taken
    for one nothing names."""
    live = live_slots()
    doc = _keys["doc"]
    if not isinstance(doc, dict) or not isinstance(doc.get("slots"), list):
        return None
    return live | {str(s) for s in doc["slots"] if isinstance(s, (str, int))}


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
    except (OSError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _write_json(path, v):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(v, f)
    os.replace(tmp, path)


def _delete_slot(stores, slot):
    """Everything a slot keeps, and its database in memory. Returns whether anything was on disk."""
    stores.pop(slot, None)
    paths = [p for p in slot_paths(slot) if os.path.lexists(p)]
    for p in paths:
        _remove(p)
    return bool(paths)


def slot_sweep(stores):
    """Carry out what the app asked when a profile was deleted (#588): everything its slot keeps is deleted, so no
    profile made later ever reads it. Never a slot a profile's secret still uploads to, nor one a profile reads. A
    profile slot's id never comes again, so it is deleted whenever anything of it is found (an upload that raced the
    deletion too). Slot "", which another profile may come to read: once for each deletion asked, and again whenever
    something is found there while no profile reads it and no secret takes it (an upload checked just before its
    secret went, #593); from an app that doesn't say which slots its profiles read, only once."""
    asked = deleted_state()
    if not asked:
        return
    live, named = live_slots(), named_slots()
    done_file = os.path.join(DATA, DELETED_DONE)
    done = _json(done_file)
    for slot, token in sorted(asked.items()):
        if slot in live or (named is not None and slot in named):
            continue
        if slot == "" and done.get("") == token and named is None:
            continue
        if _delete_slot(stores, slot):
            log(f"deleted what was kept for {slot or 'the first profile'}: its profile was deleted in the app")
        if slot == "" and done.get("") != token:
            done[""] = token
            _write_json(done_file, done)


ORPHANS = "orphan-slots.json"   # in $DATA: {slot: when it was first found with nothing naming it} (#593)


def slots_found():
    """Every slot something is kept for: "" and each profile slot with a folder (its database's or its summary's)."""
    out = set()
    for top in (os.path.join(DATA, "profiles"), os.path.join(DATA, "public", "profiles")):
        try:
            out |= {n for n in os.listdir(top) if n.isdigit()}
        except OSError:
            pass
    return sorted(s for s in out | {""} if any(os.path.lexists(p) for p in slot_paths(s)))


def orphan_sweep(stores, now=None):
    """A slot no profile reads and no secret uploads to (left by a profile deleted before #588, or by one the app
    forgot), deleted SLOT_GRACE after it's first found so (#593); found named again, it starts again. Nothing while
    $KEYS_FILE doesn't say which slots the app's profiles read."""
    now = time.time() if now is None else now
    named = named_slots()
    if named is None:
        return
    path = os.path.join(DATA, ORPHANS)
    seen = _json(path)
    found = {s: seen[s] if isinstance(seen.get(s), (int, float)) else now for s in slots_found() if s not in named}
    for slot, first in sorted(found.items()):
        if now - first >= SLOT_GRACE:
            if _delete_slot(stores, slot):
                log(f"deleted what was kept for {slot or 'the first profile'}: no profile or secret has named it since "
                    f"{datetime.fromtimestamp(first, timezone.utc).date()}")
            del found[slot]
    if found != seen:
        _write_json(path, found)


RESTORE_NAME = re.compile(r"before-restore-(\d{8}-\d{6})(?:-1)*")   # as apply_restore names them


def restore_sweep(now=None):
    """A before-restore-<time>/ folder (what was here before a restore: it may hold a profile deleted since) is
    deleted RESTORE_KEEP after it was made (#593). Only folders named as apply_restore names them."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(DATA)
    except OSError:
        return
    for n in sorted(names):
        m = RESTORE_NAME.fullmatch(n)
        path = os.path.join(DATA, n)
        if not m or os.path.islink(path) or not os.path.isdir(path):
            continue
        try:
            made = max(datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp(), os.stat(path).st_mtime)
        except (ValueError, OSError):
            continue
        if now - made >= RESTORE_KEEP:
            _remove(path)
            log(f"deleted {n}: what was here before a restore, kept {RESTORE_KEEP / 86400:g} days")


def leftover_sweep():
    """What a deletion interrupted halfway left, moved aside (_remove's .deleting- names): deleted."""
    for top in (DATA, os.path.join(DATA, "profiles"), os.path.join(DATA, "public"), os.path.join(DATA, "public", "profiles")):
        try:
            names = os.listdir(top)
        except OSError:
            continue
        for n in names:
            p = os.path.join(top, n)
            if n.startswith(".deleting-"):
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)


def start_sweep(stores=None, now=None):
    """At start, before anything is loaded, and from time to time after: what deleted profiles kept (#588), slots
    nothing names past their grace period, before-restore-<time>/ folders past their time and deletions left halfway
    (#593)."""
    stores = {} if stores is None else stores
    leftover_sweep()
    slot_sweep(stores)
    orphan_sweep(stores, now)
    restore_sweep(now)


EXERCISE_INSERT = ("INSERT OR REPLACE INTO exercise(uuid, start_t, end_t, day, type, name, title, duration, src, hr) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)")


def hae_workout_hr(w, s, e):
    """A Health Auto Export workout's heart rate (#197), as hrzones.packed writes it, or None: its minute-by-minute
    readings ("heartRateData": [{date, Min, Avg, Max}], or {date, qty}) and its average and maximum ("heartRate":
    {avg, max}, each {qty, units}; older versions "avgHeartRate" and "maxHeartRate"), the app's own figures winning."""
    rows = w.get("heartRateData") if isinstance(w.get("heartRateData"), list) else []
    readings, top = [], []
    for r in rows:
        if not isinstance(r, dict) or (t := hae_when(r.get("date"))) is None:
            continue
        v = number(r.get("Avg") if r.get("Avg") is not None else r.get("qty"))
        if v is not None:
            readings.append((int(t.timestamp()), v))
            top.append(number(r.get("Max")) or v)
    hist, avg, mx = hrzones.histogram(readings, int(s.timestamp()), int(e.timestamp()))
    hr = w.get("heartRate") if isinstance(w.get("heartRate"), dict) else {}
    a = number(hr.get("avg")) if hr.get("avg") is not None else number(w.get("avgHeartRate"))
    m = number(hr.get("max")) if hr.get("max") is not None else number(w.get("maxHeartRate"))
    if m is None and top:
        m = max(top)
    return hrzones.packed(hist, a if a is not None else avg, m if m is not None else mx) or None


def is_hae(p):
    """A Health Auto Export upload: {"data": {"metrics": [...], "workouts": [...]}}, or its ECG and heart rate
    notification automations' {"data": {"ecg": [...]}} and {"data": {"heartRateNotifications": [...]}} (#182), or its
    medications automation's {"data": {"medications": [...]}} (#235)."""
    d = p.get("data") if isinstance(p, dict) else None
    return isinstance(d, dict) and any(k in d for k in ("metrics", "workouts", "ecg", "heartRateNotifications", "medications"))


HAE_DOSE = {"taken": "taken", "skipped": "skipped"}   # Apple Health's dose statuses that say what the person logged


def hae_dose(m):
    """One of Health Auto Export's dose events ({displayText, nickname?, start, scheduledDate?, status, dosage?, units,
    ...}) as (key, start, summary row or None): the row [start with its offset, name, "taken" | "skipped", dosage, units,
    "Apple Health"]; None for a status that isn't a dose logged (Not Interacted, Notification Not Sent, Snoozed, Not
    Logged, Unspecified). The key is the medication, the start and the scheduled time, so a status changed later
    replaces the dose. None altogether when it has no name or start it can read."""
    name = " ".join(str(m.get("displayText") or m.get("nickname") or "").split())[:300]   # the drug's name, as the records list it
    t = hae_when(m.get("start"))
    if not name or t is None:
        return None
    key = hashlib.sha256(f"{name.lower()}|{t.isoformat()}|{m.get('scheduledDate') or ''}".encode()).hexdigest()[:24]
    status = HAE_DOSE.get(" ".join(str(m.get("status") or "").lower().split()))
    if status is None:
        return key, int(t.timestamp()), None
    dose = number(m.get("dosage"))
    return key, int(t.timestamp()), [t.isoformat(), name, status, dose, str(m.get("units") or "") if dose is not None else "", APP["health_auto_export"]]


def ecg_files(slot):
    """The ECG samples written beside a slot's summary (#182)."""
    d = os.path.join(os.path.dirname(summary_file(slot)), "ecg")
    return [os.path.join(d, n) for n in sorted(os.listdir(d))] if os.path.isdir(d) else []


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
    return _archive(body, slot)[0]


def _archive(body, slot):
    """(its digest, the file) of an upload archived under the slot's raw/."""
    now = datetime.now(timezone.utc)
    d = os.path.join(slot_dir(slot), "raw", now.strftime("%Y/%m/%d"))
    os.makedirs(d, exist_ok=True)
    digest = hashlib.sha256(body).hexdigest()
    path = os.path.join(d, f"{now.strftime('%H%M%S%f')}-{digest[:12]}.json.gz")
    crypto.write_file(path, gzip.compress(body))   # still named .json.gz: gzipped, then sealed
    return digest, path


def unarchive(path, slot):
    """Take an archived upload out again, and the day folders it leaves empty (never raw/ itself)."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    top, d = os.path.join(slot_dir(slot), "raw"), os.path.dirname(path)
    while d.startswith(top + os.sep):
        try:
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)


work = queue.Queue()


SWEEP_EVERY = 3600   # seconds between the worker's sweeps of slots nothing names and old restore folders (#593)


def fold(stores, item):
    """Fold one queued upload into its slot's database. Returns whether it changed anything. Not for a slot no secret
    uploads to any more (its profile deleted since the upload was checked, #593): what it kept is being deleted."""
    slot, body, digest = item
    if slot not in live_slots():
        log("not folded in: no secret uploads to", slot or "the first profile", "any more")
        return False
    try:
        if slot not in stores:
            stores[slot] = Store(slot)
        res = stores[slot].ingest(json.loads(body), digest)
        log("ingested", res, "for", slot or "first profile")
        return res not in ("skipped", "duplicate")
    except Exception as e:   # logged without what it held (quiet, #592): it stays in raw/, folded in again at next start
        log("FAILED to process payload", digest[:12], quiet(e))
        return False


def worker(stores):
    """Folds uploads in, one at a time; a slot's snapshot and summary are written once uploads stop coming."""
    dirty, swept = set(), time.monotonic()
    while True:
        try:
            item = work.get(timeout=5)
        except queue.Empty:
            try:
                slot_sweep(stores)   # (#588)
                if time.monotonic() - swept >= SWEEP_EVERY:   # (#593)
                    swept = time.monotonic()
                    orphan_sweep(stores)
                    restore_sweep()
                dirty &= set(stores)   # a deleted slot's summary isn't written again
            except Exception as e:
                log("FAILED the slots' sweep", quiet(e))
            settle(stores, dirty)
            dirty = set()
            continue
        if fold(stores, item):
            dirty.add(item[0])

def settle(stores, dirty):
    """Once uploads stop coming: the cycle sweep (#183), then each changed slot's snapshot and summary."""
    try:
        cycle_sweep(stores)
    except Exception as e:
        log("FAILED the cycle records' sweep", quiet(e))
    for slot in sorted(dirty):
        try:
            stores[slot].save(); stores[slot].write_summary(); log("summary written:", slot or "first profile")
        except Exception as e:
            log("FAILED to save", slot or "first profile", quiet(e))


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):   # in place of socketserver's traceback on stderr (#592)
        log("FAILED to answer", client_address[0], quiet(sys.exc_info()[1]))


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

    def _refuse(self, code, wait=0):
        """Answer without reading the body, and close the connection (what's left of the body is never read)."""
        self.close_connection = True
        self.send_response(code)
        if wait:
            self.send_header("Retry-After", str(wait))
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_POST(self):
        if self.path.split("?")[0] not in ("/ingest", "/ingest/"):
            return self._send(404)
        given = (self.headers.get("X-Signature") or "").strip().encode()
        auth = (self.headers.get("Authorization") or "").strip()   # Health Auto Export's header: "Bearer <secret>"
        token = (auth[7:].strip() if auth[:7].lower() == "bearer " else "").encode()
        if not given and not token:   # (#222) neither signed nor carrying a secret: refused before reading anything
            log("rejected: neither signed nor a secret, from", self.client_address[0])
            return self._refuse(401)
        n = int(self.headers.get("Content-Length") or 0) if (self.headers.get("Content-Length") or "").isdigit() else 0
        if n <= 0 or n > MAX_BODY:
            return self._refuse(413 if n > MAX_BODY else 400)
        ip, slot, body = "ip:" + self.client_address[0], None, None
        if token:   # the secret itself: checked before the body is read, every one compared (#222)
            for s, key in keys():
                if hmac.compare_digest(key, token) and slot is None:
                    slot = s
        if slot is None and given:
            if (wait := throttle.wait(ip)):   # too many bad ones from this address lately: not read (#222)
                return self._refuse(429, wait)
            body = self.rfile.read(n)
            for s, key in keys():   # every one compared, so the time taken doesn't say which matched
                expected = ("sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()).encode()
                if hmac.compare_digest(expected, given) and slot is None:
                    slot = s
        if slot is None:
            throttle.fail(ip)
            log("rejected: bad signature from", self.client_address[0])
            return self._refuse(401)
        if body is None:
            body = self.rfile.read(n)
        try:   # cycle records left out unless this profile turned tracking on, before anything is kept (#183)
            body = cycle.keep(body, cycle_on(slot))
        except ValueError:
            return self._send(400)
        digest, path = _archive(body, slot)
        if slot not in live_slots():   # its secret went while this was checked and written (#593): not kept
            unarchive(path, slot)
            log("rejected: the secret went before the upload was kept, from", self.client_address[0])
            return self._refuse(401)
        work.put((slot, body, digest))
        self._send(200, b"ok")

    def log_message(self, *a):
        pass


def reprocess():
    for slot in slots_on_disk():
        db = os.path.join(slot_dir(slot), "watch.db")
        old = Store(slot)   # the deletions it has done (#183), kept across the rebuild
        done = {k: v for k in ("cycle_purged", "cycle_purged_at") if (v := old.meta(k))}
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(db + ext):
                os.remove(db + ext)
        store = Store(slot)
        for k, v in done.items():
            store.meta(k, v)
        n = store.catch_up()   # a new database: every upload in raw/
        store.save()
        store.write_summary()
        log(f"reprocessed {len(raw_files(slot))} payloads of {slot or 'the first profile'} ({n} with new records)")


# ---- a backup's data, restored (#227): server.manage restore leaves it in keys/restore/, and it's moved in on start
RESTORED = ("watch.db", "raw", "profiles")   # what a backup holds of this folder; public/ is written again from it


def restore_dir():
    return os.path.join(os.path.dirname(KEYS_FILE), "restore")


def apply_restore():
    """Move a restored backup's data in, if server.manage restore left one: what is here now goes to
    $DATA/before-restore-<time>/ (deleted RESTORE_KEEP after, by restore_sweep, #593), with public/'s files (the
    folder stays: the app has it mounted by itself), which are written again from the restored databases. Returns
    that folder, or None."""
    src = restore_dir()
    if not os.path.isfile(os.path.join(src, "restore.json")):
        return None
    aside = os.path.join(DATA, "before-restore-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    while os.path.exists(aside):
        aside += "-1"
    os.makedirs(aside)
    for n in RESTORED + ("watch.db-wal", "watch.db-shm"):
        if os.path.lexists(os.path.join(DATA, n)):
            shutil.move(os.path.join(DATA, n), os.path.join(aside, n))
    pub = os.path.join(DATA, "public")
    for n in os.listdir(pub) if os.path.isdir(pub) else ():
        os.makedirs(os.path.join(aside, "public"), exist_ok=True)
        shutil.move(os.path.join(pub, n), os.path.join(aside, "public", n))
    for n in RESTORED:
        if os.path.lexists(os.path.join(src, n)):
            shutil.move(os.path.join(src, n), os.path.join(DATA, n))
    shutil.rmtree(src)
    log(f"restored the phone and watch data from a backup; what was here is in {aside}")
    return aside


def settings_changed():
    """Whether receiver-settings.json is another than the one read at start (made, changed or removed)."""
    return _settings_sig() != SETTINGS_SIG


def settings_watch(srv, every=5, wait=60):
    """(#1121) Restart on a change to the settings the app copied here: once the uploads waiting are folded in (at most
    wait seconds; any left are archived already, and folded in at the next start), the server stops and the receiver
    exits."""
    while not settings_changed():
        time.sleep(every)
    log("its settings changed (Settings → General on the app): restarting to read them")
    until = time.monotonic() + wait
    while not work.empty() and time.monotonic() < until:
        time.sleep(0.5)
    srv.shutdown()


def load_key():
    try:
        crypto.keyring.load()
    except crypto.KeyError_ as e:
        sys.exit(f"{e} The setup service makes it (docker compose up); the receiver uses the app's key.")


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    load_key()
    apply_restore()   # a backup's data, restored with server.manage restore (#227)
    if "--reprocess" in sys.argv:
        reprocess(); sys.exit(0)
    if secret() is None and not os.path.isfile(KEYS_FILE):   # fail fast when there's no secret at all
        sys.exit(f"No secret: neither {SECRET_FILE} nor {KEYS_FILE} exists.")
    if (n := migrate()):
        log(f"encrypted {n} files with the current key")
    start_sweep()   # what deleted profiles kept (#588), slots nothing names and old restore folders (#593)
    stores, named = {}, named_slots()
    for slot in slots_on_disk():
        if slot == "" and named is not None and "" not in named and "" not in slots_found():
            continue   # nothing kept for slot "" and nothing names it: no empty summary written for it (#593)
        store = stores[slot] = Store(slot)
        if (n := store.catch_up()):
            log(f"folded in {n} uploads of {slot or 'the first profile'} archived since the last snapshot")
            store.save(); store.write_summary()
        elif not os.path.exists(summary_file(slot)):
            store.write_summary()
    cycle_sweep(stores)   # cycle records of profiles that haven't turned tracking on: none kept, not even archived (#183)
    log(f"taking uploads signed with {len(keys())} secret(s)")
    threading.Thread(target=worker, args=(stores,), daemon=True).start()
    log(f"listening on :{PORT}")
    srv = Server(("0.0.0.0", PORT), H)
    threading.Thread(target=settings_watch, args=(srv,), daemon=True).start()
    srv.serve_forever()
    sys.exit(0)   # its settings changed: Docker's restart policy starts it again, reading them
