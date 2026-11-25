"""Export everything in a profile as one zip, and load such a zip into a profile.

The zip:
  README.txt          what this is and how to load it again
  data.json           the records as they were imported (records.assemble(raw=True), #589): hidden and merged-in ones
                      too, with their own titles and dates, and each document link pointing at the copy in documents/;
                      the corrections made to them in the app as "corrections" (corrections(): the edits, the
                      same-result decisions and the Costs marks, each by the key it finds its record by, so they apply
                      again once loaded and can still be undone); the journal (notes, symptoms, side effects and moods
                      people wrote, journal.py) as its "journal" list, each photo an entry lists naming its file below
                      ("file"); the medication log (#235, med_log.py) as "doses" and medication reminders (#162) as
                      "reminders". Loading the export puts all of these back (restore())
  journal/photos/<id>.<jpg|png|webp>  the journal's photos (#161), as kept: their metadata was taken out when sent
  settings.json      the settings set for the profile, shaped like settings.example.json (none: all at their defaults)
  documents/<id>/...  every document, and for HTML records the pictures and stylesheets they show
  watch/daily.json    the watch and scale summary shown with the profile (if any): its daily measures, home blood
                      pressure and temperature readings, glucose, workouts, ECGs and irregular rhythm notifications
  watch/ecg/<16 hex>.json  each ECG recording's samples (#182), the file the summary's ECG of that key draws (#225):
                      the person's data like the rest; loading the export keeps them beside its daily.json again
  uploads/<id>/...    the files uploaded on the import page, as they were (zips unpacked), one folder per upload
  activity.json       the profile's activity log, only in an owner's export (#589): like GET .../activity, it is for
                      owners only (who opened the profile, from where)
  cycle/cycle.json    menstrual cycle records (#183), only when the person exporting ticked them in, each time
                      (cycle_tracking.for_export); loading an export leaves them out, as cycle tracking is per install
It's written as it's sent, a piece at a time, so a large profile never sits in memory or on disk.

What doesn't come back from an export (the Sharing page says so): the activity log (history from the other install),
who is shared on it, share links, calendar feeds, connected sources and their sign-ins, the watch receiver's key,
visit summary drafts, and which import each record came from (they load as one import, "Sanguity data or export").
"""
import contextlib, datetime, gzip, json, os, re, secrets, shutil, urllib.parse, zipfile

from . import (crossmatch, crypto, cycle_tracking, db as dbm, ecg, edits, journal, journal_photos, med_log, med_reminders, oop,
               profiles as prof, records, settings, uploads, watch_store)

DOC_URL = re.compile(r"^api/profiles/\d+/documents/([^/#?]+)")
REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#?]+)""", re.I)
STORED = ("pdf", "jpg", "jpeg", "png", "gif", "webp", "zip", "gz")   # already compressed


def plan(db, pid, cycle=False, activity=False, shown=False):
    """Everything the export needs, gathered now: the stream runs after the request's database connection closes.
    cycle: the cycle records too, ticked in for this export (#183). activity: the activity log too, for an owner's
    export only (#589: an editor can't read it, GET .../activity). shown: the records as the app shows them instead
    (records.served: every correction and same-result link applied, no "corrections" beside them), for the FHIR export
    (#916), which has nowhere to carry the corrections and nothing on the other side to apply them."""
    p = db.execute("SELECT name, watch_file FROM profiles WHERE id=?", (pid,)).fetchone()
    docs = {r["id"]: (crypto.dec(r["path"]), r["ext"]) for r in db.execute("SELECT id, path, ext FROM documents WHERE profile_id=?", (pid,))}
    log = None
    if activity:
        log = [dict(r) for r in db.execute("SELECT at, actor, action, detail, ip FROM activity WHERE profile_id=? ORDER BY id", (pid,))]
        for e in log:
            e["detail"] = json.loads(crypto.dec(e["detail"])) if e["detail"] else {}
            e["detail"].pop("key", None)
    if shown:
        data = json.loads(records.served(db, pid) or b"null")   # a copy of what the page reads, its own to change
    else:
        data = records.assemble(db, pid, raw=True)   # as imported: what loading the export stores again (#589)
    if data is None and not shown:   # nothing imported, but what people wrote or decided still goes (#589)
        own = {k: v for k, v in (("journal", journal.listing(db, pid)), ("reminders", med_reminders.listing(db, pid)),
                                 ("doses", med_log.listing(db, pid))) if v}
        data = {"events": [], "details": {}, "labs": [], "costs": [], **own} if own or _has_corrections(db, pid) else None
    if data is not None and not shown and (fixes := corrections(db, pid, data["events"])):
        data["corrections"] = fixes
    watch =watch_store.load(db, pid)   # the merged summary (receiver + imports), or None
    waves = {}   # ECG samples (#225): file name -> where they are, for each recording the summary lists
    for e in (watch or {}).get("ecg") or []:
        name = ecg.file_name(e.get("key")) if isinstance(e, dict) else None
        path = watch_store.wave_path(db, pid, name[:-8]) if name else None
        if path:
            waves[name[:-8]] = path
    return {"pid": pid, "name": p["name"], "docs": docs, "activity": log,
            "data": data, "uploads": uploads.stored_files(db, pid),
            "settings": settings.to_file(db, pid), "watch": watch, "waves": waves,
            "cycle": cycle_tracking.for_export(db, pid) if cycle else None,
            "photos": journal_photos.stored(db, pid)}   # #161


def _dec(text):
    return None if text is None else json.loads(crypto.dec(text))


def _has_corrections(db, pid):
    return any(db.execute(f"SELECT 1 FROM {t} WHERE profile_id=? LIMIT 1", (pid,)).fetchone()
               for t in ("edits", "same_results", "cost_marks"))


def corrections(db, pid, events):
    """What people decided about the records, as data.json's "corrections" (#589), or None when nothing was:
      edits  [{action, key, other?, value, at, by, moved?}]  edits.py's corrections, by the key of the event each
             finds (key) and, for a merge, of the one merged in (other); a rule for new imports (kind_rule) with the keys
             of the entries it moves now (moved), as those load as an import of their own, with a source of their own
      same   [{a, b, same, value, at, by}]   crossmatch.py's decisions, by both results' point keys
      marks  [{key, value, at, by}]          the Costs tab's marks (oop.py), by the payment's or claim's key
    events: the records as imported (assemble(raw=True)), which a rule's entries are found in."""
    users = {r["id"]: r["username"] for r in db.execute("SELECT id, username FROM users")}
    out = {"edits": [], "same": [], "marks": []}
    rules = {}
    for r in db.execute("SELECT id, target_key, action, other_key, value, created_by, at FROM edits WHERE profile_id=? ORDER BY id", (pid,)):
        value = _dec(r["value"]) or {}
        x = {"action": r["action"], "key": crypto.dec(r["target_key"]), "value": value, "at": r["at"],
             "by": users.get(r["created_by"]) or value.get("by")}
        if r["other_key"]:
            x["other"] = crypto.dec(r["other_key"])
        if r["action"] == edits.RULE:
            rules[r["id"]] = x
        out["edits"].append(x)
    if rules:
        shown = json.loads(records.served(db, pid) or b"{}")   # the edits as applied: the entries each rule moved
        keys = {e["id"]: edits.key_of(e) for e in events if isinstance(e, dict) and "id" in e}
        for e in shown.get("edits") or []:
            if e.get("id") in rules:
                rules[e["id"]]["moved"] = [keys[i] for i in e.get("ids") or [] if i in keys]
    for r in db.execute("SELECT a_key, b_key, same, value, created_by, at FROM same_results WHERE profile_id=? ORDER BY id", (pid,)):
        value = _dec(r["value"]) or {}
        out["same"].append({"a": crypto.dec(r["a_key"]), "b": crypto.dec(r["b_key"]), "same": bool(r["same"]), "value": value,
                            "at": r["at"], "by": users.get(r["created_by"]) or value.get("by")})
    for r in db.execute("SELECT target_key, value, created_by, at FROM cost_marks WHERE profile_id=? ORDER BY id", (pid,)):
        value = _dec(r["value"]) or {}
        out["marks"].append({"key": crypto.dec(r["target_key"]), "value": value, "at": r["at"],
                             "by": users.get(r["created_by"]) or value.get("by")})
    return out if any(out.values()) else None


def filename(name, now=None):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "profile"
    return f"sanguity-{slug}-{(now or datetime.datetime.now()):%Y%m%d}.zip"


class _Sink:
    """Where the zip is written: collects bytes until the stream hands them on. Not seekable, so zipfile writes
    each entry's sizes after its data (data descriptors) and nothing has to be revisited."""
    def __init__(self):
        self.buf, self.pos = bytearray(), 0

    def write(self, b):
        self.buf += b
        self.pos += len(b)
        return len(b)

    def tell(self):
        return self.pos

    def flush(self):
        pass

    def take(self):
        out = bytes(self.buf)
        self.buf.clear()
        return out


def _parts(path):
    """The pictures and stylesheets an HTML record links to inside its own folder, as paths relative to it."""
    folder, out = os.path.dirname(path), []
    try:
        html = crypto.read_file(path)[:5_000_000].decode("utf-8", "replace")
    except OSError:
        return out
    for ref in dict.fromkeys(REF.findall(html)):
        rel = urllib.parse.unquote(ref.strip())
        if ":" in rel or rel.startswith(("/", "\\")):
            continue
        target = os.path.join(folder, *rel.split("/"))
        ext = os.path.splitext(target)[1].lstrip(".").lower()
        if ext in prof.PART_TYPES and prof._inside(target, folder) and os.path.isfile(target):
            out.append(os.path.relpath(os.path.realpath(target), os.path.realpath(folder)).replace(os.sep, "/"))
    return out


def stream(p, exported_by, now=None):
    now = now or datetime.datetime.now()   # the exporting person's wall-clock time (settings.local_now)
    stamp = now.timetuple()[:6]
    sink = _Sink()
    zf = zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, compresslevel=6)

    def info(name, compress=True):
        i = zipfile.ZipInfo(name, stamp)
        i.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        i.external_attr = 0o600 << 16
        return i

    def add_bytes(name, data):
        zf.writestr(info(name), data)
        return sink.take()

    def add_file(name, path, compress=True):
        # Sanguity's own copies are decrypted (an export is plaintext on purpose): a stream a chunk at a time, so
        # a large upload never sits in memory whole
        with zf.open(info(name, compress), "w", force_zip64=True) as dst:
            for piece in crypto.iter_file(path):
                dst.write(piece)
                yield sink.take()
        yield sink.take()

    # records, with document links pointing into the zip
    missing, included = [], {}
    if p["data"]:
        data = p["data"]
        for d in (data.get("details") or {}).values():
            if not isinstance(d, dict) or d.get("type") != "file" or not d.get("url"):
                continue
            m = DOC_URL.match(d["url"])
            doc = p["docs"].get(m.group(1)) if m else None
            if not doc or not os.path.isfile(doc[0]):
                missing.append(d["url"])
                d["url"] = None
                continue
            frag = d["url"].split("#", 1)[1] if "#" in d["url"] else ""
            name = f"documents/{m.group(1)}/{os.path.basename(doc[0])}"
            included[m.group(1)] = (name, doc)
            d["url"] = name + ("#" + frag if frag else "")   # a plain path, as build.py writes them
        names = {i: journal_photos.export_name(i, kind) for i, kind, path in p.get("photos") or [] if os.path.isfile(path)}
        for j in data.get("journal") or []:   # #161: each photo names its file in the zip
            for ph in j.get("photos") or []:
                ph["file"] = names.get(ph.get("id"))
        yield add_bytes("data.json", json.dumps(data, separators=(",", ":")).encode())
    # always there, even with nothing set, so loading the export back in (import_dataset) restores the settings exactly
    yield add_bytes("settings.json", json.dumps(p["settings"], indent=1, ensure_ascii=False).encode())
    for doc_id, (name, (path, ext)) in included.items():
        yield from add_file(name, path, ext not in STORED)
        if ext in prof.HTML_DOCS:
            for rel in _parts(path):
                yield from add_file(f"documents/{doc_id}/{rel}", os.path.join(os.path.dirname(path), *rel.split("/")),
                                    not rel.lower().endswith(STORED))
    if p["watch"]:
        yield add_bytes("watch/daily.json", json.dumps(p["watch"], separators=(",", ":")).encode())
    for name, path in sorted((p.get("waves") or {}).items()):   # each ECG's samples, as plain JSON (#225)
        try:
            raw = gzip.decompress(crypto.read_file(path))
        except (OSError, ValueError, EOFError, crypto.KeyError_):
            continue
        yield add_bytes(f"watch/ecg/{name}.json", raw)
    if p.get("cycle"):   # ticked in for this export (#183)
        yield add_bytes("cycle/cycle.json", json.dumps(p["cycle"], indent=1, ensure_ascii=False).encode())
    for upload_id, rel, path in p["uploads"]:
        if os.path.isfile(path):
            yield from add_file(f"uploads/{upload_id}/{rel}", path, not rel.lower().endswith(STORED))
    for photo_id, kind, path in p.get("photos") or []:   # #161: pictures are compressed already
        if os.path.isfile(path):
            yield from add_file(journal_photos.export_name(photo_id, kind), path, False)
    if p["activity"] is not None:   # an owner's export only (#589)
        yield add_bytes("activity.json", json.dumps(p["activity"], indent=1).encode())
    got = p["data"] or {}
    fixes = got.get("corrections") or {}
    readme = f"""Sanguity export: {p['name']}
Exported {now:%Y-%m-%d %H:%M} by {exported_by}.

This file holds someone's health records. Keep it somewhere private, ideally encrypted, and delete copies you
no longer need.

  data.json          the records as they were imported ({len(included)} documents linked), hidden and merged ones too,
                     with the corrections made to them in Sanguity ("corrections": {len(fixes.get('edits') or [])} edits, {len(fixes.get('same') or [])} same-result
                     decisions, {len(fixes.get('marks') or [])} Costs marks), the journal ("journal": {len(got.get('journal') or [])} entries),
                     the medication log ("doses": {len(got.get('doses') or [])} logged; a phone's are in watch/daily.json) and
                     the medication reminders ("reminders": {len(got.get('reminders') or [])})
  settings.json     the profile's settings{'' if len(p['settings']) > 1 else ' (none set: all at their defaults)'}
  documents/         the documents, one folder each
  watch/daily.json   watch and scale summary{'' if p['watch'] else ' (none)'}
{f"  watch/ecg/         each ECG recording's samples, by the key in daily.json: {len(p['waves'])}" + chr(10) if p.get('waves') else ''}{"  cycle/cycle.json   menstrual cycle records from the phone apps, ticked in for this export" + chr(10) if p.get('cycle') else ''}{f"  journal/photos/    the photos attached to journal entries: {len(p['photos'])}" + chr(10) if p.get('photos') else ''}  uploads/           the files uploaded to import, as they were sent (zips unpacked): {len({u for u, _, _ in p['uploads']})} upload(s)
{"  activity.json      who opened, shared or changed this profile, and when" + chr(10) if p['activity'] is not None else ''}{f'''
{len(missing)} document link(s) pointed at files that are no longer on the server and are left out.
''' if missing else ''}
To load it into a Sanguity install (into an empty profile, or replacing one's records):
  python -m server.manage create-profile "Name" USERNAME      (prints the new profile's number)
  python -m server.manage import PROFILE_NUMBER /path/to/this.zip
That brings back the records, the corrections made to them (which can still be undone there), the journal and its
photos, the medication log and reminders, the settings, documents, watch data and uploads. Uploaded on a profile's
import page instead, the records with their documents, corrections, journal, medication log and reminders are added
to that profile's. Neither brings back the activity log, who the profile is shared with, share links, calendar
feeds, connected sources and their sign-ins, the watch receiver's key, or which import each record came from (they
arrive as one import).
"""
    yield add_bytes("README.txt", readme.encode())
    zf.close()
    yield sink.take()


# ---------------------------------------------------------------- loading an export

ALLOWED = re.compile(r"^(data\.json|settings\.json|README\.txt|activity\.json|watch/daily\.json|watch/ecg/[0-9a-f]{16}\.json|"
                     r"cycle/cycle\.json|documents/[^/]+/.+|uploads/\d+/.+|journal/photos/\d+\.(?:jpg|png|webp))$")
WAVE = re.compile(r"^watch/ecg/([0-9a-f]{16})\.json$")   # (#225) kept as the app keeps them: gzipped, then sealed
LEFT_OUT = {"cycle/cycle.json"}   # cycle records (#183): not loaded into the profile, nor kept in its folder
UPLOAD = re.compile(r"^uploads/(\d+)/(.+)$")
# a zip that unpacks to more than this is refused before anything is written (a damaged or hostile archive could
# otherwise fill the disk); far above any real profile's records
MAX_ENTRIES, MAX_BYTES = 100_000, 16 * 2**30


PARTS = ("journal", "doses", "reminders")   # what people wrote, beside the records and their corrections
KEY_MAX, BY_MAX = 4000, 200


def _fail(what, n, why):
    raise ValueError(f"The export's {what} {n + 1} can't be loaded: {why}")


def _list(data, *path):
    v = data
    for k in path:
        v = v.get(k) if isinstance(v, dict) else None
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError(f"The export's {'.'.join(path)} isn't a list, so it can't be loaded.")
    return v


def _str(v, limit, what, empty=False):
    if not isinstance(v, str) or (not empty and not v.strip()) or len(v) > limit:
        raise ValueError(f"its {what} isn't text of at most {limit} characters")
    return v


def _stamp(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2**42 else None


def _who(x):
    """(the username it was by, when it was made, when changed and by whom) from a listed entry, as kept."""
    by = x.get("by") if isinstance(x.get("by"), str) else ""
    changed_by = x.get("changed_by") if isinstance(x.get("changed_by"), str) else None
    return by[:BY_MAX], _stamp(x.get("at")), _stamp(x.get("changed")), changed_by[:BY_MAX] if changed_by else None


def _edit(x):
    """A correction as the edits table keeps it: (action, key, other key, value), or ValueError saying why not."""
    if not isinstance(x, dict):
        raise ValueError("it isn't an object")
    action = x.get("action")
    if action not in edits.ACTIONS + (edits.RULE,):
        raise ValueError("it isn't a kind of correction Sanguity makes")
    key = _str(x.get("key"), KEY_MAX, "key")
    value = x.get("value") if isinstance(x.get("value"), dict) else None
    if value is None:
        raise ValueError("it says nothing of what was changed")
    other = _str(x.get("other"), KEY_MAX, "other key") if action == "merge" else None
    if other == key:
        raise ValueError("it merges a record into itself")
    if action == "rename":
        _str(value.get("title"), edits.TITLE_MAX, "title")
    elif action == "date" and not (isinstance(value.get("t"), str) and edits._TIME.match(value["t"]) and edits._parse(value["t"])):
        raise ValueError("its date isn't one")
    elif action in ("kind", edits.RULE) and value.get("k") not in edits.MOVABLE:
        raise ValueError("it moves the record to a category Sanguity doesn't have")
    if action == edits.RULE:
        _str(value.get("source"), KEY_MAX, "source")
        _str(value.get("title"), edits.TITLE_MAX, "title", empty=True)
        if value.get("from") not in edits.MOVABLE:
            raise ValueError("it moves records from a category Sanguity doesn't have")
    moved = [_str(k, KEY_MAX, "entry's key") for k in _list(x, "moved")] if action == edits.RULE else []
    return action, key, other, value, moved


def _entry(e):
    """A journal entry's body as journal.check stores it, without its photos, or JournalError/ValueError."""
    if not isinstance(e, dict):
        raise ValueError("it isn't an object")
    kind = e.get("kind")
    if kind not in journal.KINDS:
        raise ValueError("it isn't a note, a symptom, a side effect or a mood")
    t = journal._when(e.get("t"), kind == "mood")
    note = journal._text(e.get("note"), journal.NOTE_MAX, "note")
    if kind == "mood":
        mood, energy = journal._scale(e.get("mood"), "Mood"), journal._scale(e.get("energy"), "Energy")
        if mood is None and energy is None:
            raise ValueError("a mood entry with no mood or energy level")
        return {"kind": kind, "t": t, "mood": mood, "energy": energy, "note": note}
    title = journal._text(e.get("title"), journal.TITLE_MAX, "title")
    if kind == "note":
        if not note:
            raise ValueError("a note with nothing written in it")
        return {"kind": kind, "t": t, "title": title, "note": note}
    if not title:
        raise ValueError("it doesn't say what the symptom or side effect was")
    severity = e.get("severity")
    if isinstance(severity, bool) or not isinstance(severity, int) or not 0 <= severity <= 10:
        raise ValueError("its severity isn't a whole number from 0 to 10")
    body = {"kind": kind, "t": t, "title": title, "severity": severity, "note": note,
            "med": journal._text(e.get("med"), journal.MED_MAX, "medication") or None}
    if (where := journal._where(e.get("where"))):
        body["where"] = where
    if (group := " ".join(journal._text(e.get("group"), journal.GROUP_MAX, "group's name").split())):
        body["group"] = group
    return body


def _dose(d):
    if not isinstance(d, dict):
        raise ValueError("it isn't an object")
    t = d.get("t")
    if not (isinstance(t, str) and med_log._TIME.match(t)):
        raise ValueError("its time isn't YYYY-MM-DDTHH:MM")
    try:
        datetime.datetime.strptime(t, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError("its time doesn't exist") from None
    if d.get("status") not in med_log.STATUSES:
        raise ValueError("it isn't taken, taken late or skipped")
    slot = d.get("slot")
    if slot is not None and not (isinstance(slot, str) and med_log._SLOT.match(slot)):
        raise ValueError("its reminder time isn't HH:MM")
    return {"med": _str(d.get("med"), 300, "medication"), "name": _str(d.get("name") or d["med"], 300, "medication's name"),
            "t": t, "status": d["status"], "note": _str(d.get("note") or "", med_log.NOTE_MAX, "note", empty=True).strip(),
            "slot": slot or None}


def _reminder(r):
    if not isinstance(r, dict):
        raise ValueError("it isn't an object")
    times = r.get("times")
    if not isinstance(times, list) or not times or len(set(times)) > med_reminders.TIMES_MAX or \
            not all(isinstance(t, str) and med_reminders._TIME.match(t) for t in times):
        raise ValueError(f"its times aren't 1 to {med_reminders.TIMES_MAX} times of day as HH:MM")
    days = r.get("days")
    if not isinstance(days, list) or not days or not all(d in med_reminders.DAYS for d in days):
        raise ValueError("its days aren't days of the week")
    a = med_reminders._date(r.get("from"), "from")
    b = med_reminders._date(r["until"], "until") if r.get("until") else None
    if b and b < a:
        raise ValueError("its until date is before its from date")
    tz = r.get("tz") or ""
    if tz:
        settings.time_zone(_str(tz, 64, "time zone"))   # SettingError (a ValueError) for one this server doesn't know
    return {"med": _str(r.get("med"), 300, "medication"), "name": _str(r.get("name") or r["med"], 300, "medication's name"),
            "times": sorted(set(times)), "days": [d for d in med_reminders.DAYS if d in days], "from": a.isoformat(),
            "until": b.isoformat() if b else None, "tz": tz}


def check_parts(data):
    """What an export's data.json brings besides its records, checked (#589): {"edits", "same", "marks", "journal",
    "doses", "reminders"}, each a list of what its table keeps, or ValueError saying which one can't be loaded and
    why (an export Sanguity wrote always loads; a damaged or hand-made one is refused whole, before anything is
    kept). Needs no database: the importer checks an upload with it before its review (importers/sanguity.py)."""
    out = {k: [] for k in ("edits", "same", "marks", "journal", "doses", "reminders")}
    checks = (("edits", ("corrections", "edits"), "correction", _edit),
              ("same", ("corrections", "same"), "same-result decision", None),
              ("marks", ("corrections", "marks"), "Costs mark", None),
              ("journal", ("journal",), "journal entry", _entry),
              ("doses", ("doses",), "logged dose", _dose),
              ("reminders", ("reminders",), "medication reminder", _reminder))
    for name, path, what, check in checks:
        for n, x in enumerate(_list(data, *path)):
            try:
                if name == "same":
                    if not isinstance(x, dict) or not isinstance(x.get("same"), bool) or not isinstance(x.get("value") or {}, dict):
                        raise ValueError("it doesn't say whether the two are the same")
                    a, b = _str(x.get("a"), KEY_MAX, "first result's key"), _str(x.get("b"), KEY_MAX, "second result's key")
                    if a == b:
                        raise ValueError("it links a result to itself")
                    out[name].append((x, (a, b, x["same"], x.get("value") or {})))
                elif name == "marks":
                    value = x.get("value") if isinstance(x, dict) else None
                    if not isinstance(value, dict) or not value or any(k not in ("hsa", "claim", "same") for k in value):
                        raise ValueError("it isn't a mark the Costs tab makes")
                    out[name].append((x, (_str(x.get("key"), KEY_MAX, "key"), value)))
                else:
                    if name == "journal":
                        _list(x, "photos")
                        if len(x.get("photos") or []) > journal_photos.MAX_PER_ENTRY:
                            raise ValueError(f"it has more than {journal_photos.MAX_PER_ENTRY} photos")
                    out[name].append((x, check(x)))
            except ValueError as e:   # JournalError, ReminderError and SettingError among them
                _fail(what, n, str(e).rstrip(".") + ".")
    return out


def brings(data):
    """What an export's data.json brings besides the records, counted: {"corrections", "journal", "photos", "doses",
    "reminders"} with those it has (each the number in the file: what the profile has already is left as it is)."""
    got = check_parts(data)
    out = {"corrections": len(got["edits"]) + len(got["same"]) + len(got["marks"]), "journal": len(got["journal"]),
           "photos": sum(len(x.get("photos") or []) for x, _ in got["journal"]), "doses": len(got["doses"]),
           "reminders": len(got["reminders"])}
    return {k: n for k, n in out.items() if n}


def restore(db, pid, data, photo, written):
    """Put back what an export's data.json brings besides the records (#589): its corrections, same-result decisions and
    Costs marks (which find their records by key, so they apply to the records loaded with them, and can be undone
    like any made here), the journal with its photos, the medication log and reminders. Call inside the transaction
    that loads the records. photo(file): the bytes of a photo the zip names ("journal/photos/<id>.jpg"), or None;
    written: a list the photos' file names are added to as they're written, so a caller whose transaction fails
    removes them (photo_files). Each keeps who made it and when, by name: their accounts are on the other install.

    Nothing of the profile's own is changed: a correction of the same record (the same kind of edit), a decision on
    the same pair, a mark on the same payment, or the same entry (written by the same person at the same moment) is
    left as the profile has it, and counted as "here". Returns {"corrections", "journal", "photos", "doses",
    "reminders", "here", "photos_missing"}: what was put back."""
    got = check_parts(data)
    n = dict.fromkeys(("corrections", "journal", "photos", "doses", "reminders", "here", "photos_missing"), 0)

    # corrections, by the keys of the records they change
    have = {(r["target"], r["action"], r["other"]) for r in db.execute("SELECT target, action, other FROM edits WHERE profile_id=?", (pid,))}
    events = {}
    for e in data.get("events") or []:
        if isinstance(e, dict):
            events.setdefault(edits.key_of(e), e)

    def add_edit(action, key, other, value, at):
        mark = (edits.target_hash(key), action, edits.target_hash(other) if other else None)
        if mark in have:
            n["here"] += 1
            return False
        have.add(mark)
        edits._insert(db, pid, None, action, key, value, other, at or dbm.now())
        n["corrections"] += 1
        return True

    for x, (action, key, other, value, moved) in got["edits"]:
        value = {**value, **({"by": x["by"][:BY_MAX]} if isinstance(x.get("by"), str) and x["by"] else {})}
        if action == edits.RULE:
            value.setdefault("batch", secrets.token_hex(8))   # undoing the rule undoes the entries it moved with it
        if not add_edit(action, key, other, value, _stamp(x.get("at"))) or action != edits.RULE:
            continue
        # the entries the rule moved come back from another source than the rule's, so each is moved on its own
        for k in moved:
            e = events.get(k)
            if e is not None:
                if add_edit("kind", k, None, {"was": edits._was(e), "k": value["k"], "batch": value["batch"],
                                              **({"by": value["by"]} if value.get("by") else {})}, _stamp(x.get("at"))):
                    n["corrections"] -= 1   # counted with its rule
    pairs = {r[0] for r in db.execute("SELECT pair FROM same_results WHERE profile_id=?", (pid,))}
    for x, (a, b, same, value) in got["same"]:
        pair = crossmatch.pair_hash(a, b)
        if pair in pairs:
            n["here"] += 1
            continue
        pairs.add(pair)
        value = {**value, **({"by": x["by"][:BY_MAX]} if isinstance(x.get("by"), str) and x["by"] else {})}
        db.execute("""INSERT INTO same_results(profile_id, pair, a_key, b_key, same, value, created_by, at)
                      VALUES(?, ?, ?, ?, ?, ?, NULL, ?)""",
                   (pid, pair, crypto.enc(a), crypto.enc(b), int(same), _enc(value), _stamp(x.get("at")) or dbm.now()))
        n["corrections"] += 1
    marked = {r[0] for r in db.execute("SELECT target FROM cost_marks WHERE profile_id=?", (pid,))}
    for x, (key, value) in got["marks"]:
        th = oop.target_hash(key)
        if th in marked:
            n["here"] += 1
            continue
        marked.add(th)
        db.execute("INSERT INTO cost_marks(profile_id, target, target_key, value, created_by, at) VALUES(?, ?, ?, ?, NULL, ?)",
                   (pid, th, crypto.enc(key), _enc(value), _stamp(x.get("at")) or dbm.now()))
        n["corrections"] += 1

    # what people wrote: the same entry (by the same person, at the same moment) isn't added twice, nor a second mood
    # for a day
    def seen(table):
        out = set()
        for r in db.execute(f"SELECT body, created, created_name FROM {table} WHERE profile_id=?", (pid,)):
            body = _dec(r["body"]) or {}
            body.pop("photos", None)
            out.add(_same(body, r["created"], r["created_name"]))
        return out

    entries = seen("journal")
    moods = {b["t"] for b in (json.loads(k[0]) for k in entries) if b.get("kind") == "mood"}
    for x, body in got["journal"]:
        by, at, changed, changed_by = _who(x)
        k = _same(body, at, by)
        if k in entries or (body["kind"] == "mood" and body["t"] in moods):
            n["here"] += 1
            continue
        entries.add(k)
        if body["kind"] == "mood":
            moods.add(body["t"])
        jid = db.execute("""INSERT INTO journal(profile_id, body, created_by, created_name, created, changed_by, changed_name, changed)
                            VALUES(?, ?, NULL, ?, ?, NULL, ?, ?)""", (pid, _enc(body), by, at or dbm.now(), changed_by, changed)).lastrowid
        n["journal"] += 1
        pics = []
        for ph in (x.get("photos") or []) if body["kind"] != "mood" else []:
            raw = photo(ph.get("file")) if isinstance(ph, dict) and isinstance(ph.get("file"), str) else None
            if raw is None:
                n["photos_missing"] += 1
                continue
            if len(raw) > journal_photos.MAX_BYTES:
                raise ValueError(f"A photo in the export's journal ({ph['file']}) is larger than this server keeps.")
            try:
                clean, kind, _ = journal_photos.strip(raw)   # checked as a picture, its metadata out (again)
            except journal_photos.PhotoError as e:
                raise ValueError(f"A photo in the export's journal ({ph['file']}) can't be kept: {e}") from None
            alt = " ".join(ph["alt"].split())[:journal_photos.ALT_MAX] if isinstance(ph.get("alt"), str) else ""
            os.makedirs(journal_photos.folder(pid), mode=0o700, exist_ok=True)
            file = secrets.token_hex(16) + ".img"
            written.append(file)
            crypto.write_file(os.path.join(journal_photos.folder(pid), file), clean)
            ph_id = db.execute("""INSERT INTO journal_photos(profile_id, entry_id, file, type, bytes, created, created_by)
                                  VALUES(?, ?, ?, ?, ?, ?, NULL)""", (pid, jid, file, kind, len(clean), at or dbm.now())).lastrowid
            pics.append({"id": ph_id, "alt": alt})
            n["photos"] += 1
        if pics:
            db.execute("UPDATE journal SET body=? WHERE id=?", (_enc({**body, "photos": pics}), jid))
    for table, name in (("med_doses", "doses"), ("med_reminders", "reminders")):
        kept = seen(table)
        for x, body in got[name]:
            by, at, changed, changed_by = _who(x)
            k = _same(body, at, by)
            if k in kept:
                n["here"] += 1
                continue
            kept.add(k)
            if table == "med_reminders":
                db.execute("""INSERT INTO med_reminders(profile_id, body, active, created_by, created_name, created, changed_by,
                              changed_name, changed) VALUES(?, ?, ?, NULL, ?, ?, NULL, ?, ?)""",
                           (pid, _enc(body), 0 if x.get("on") is False else 1, by, at or dbm.now(), changed_by, changed))
            else:
                db.execute("""INSERT INTO med_doses(profile_id, body, created_by, created_name, created, changed_by, changed_name,
                              changed) VALUES(?, ?, NULL, ?, ?, NULL, ?, ?)""", (pid, _enc(body), by, at or dbm.now(), changed_by, changed))
            n[name] += 1
    if any(n[k] for k in ("corrections", "journal", "doses", "reminders")):
        records._bump(db, pid)
    return n


def _enc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _same(body, at, by):
    return json.dumps(body, sort_keys=True, ensure_ascii=False), at, by or ""


@contextlib.contextmanager
def photo_files(pid):
    """The list restore() adds the journal photos it writes to; removed again if what's inside fails (the transaction
    that would have kept their rows was rolled back)."""
    written = []
    try:
        yield written
    except BaseException:
        for f in written:
            journal_photos._unlink(pid, f)
        raise


def import_archive(db, pid, archive):
    """Unpack an export into the profile's own folder (so its documents stay) and load it: the records, and what
    restore() puts back with them, in one transaction. Returns the number of documents. Its uploads become the
    profile's uploads again, ready, as they were. The activity log in the zip is history from the other install and
    isn't merged in."""
    dest = os.path.join(prof.profile_dir(pid), "imported")
    new = dest + ".new"
    shutil.rmtree(new, ignore_errors=True)
    with zipfile.ZipFile(archive) as zf:
        files = [i for i in zf.infolist() if not i.is_dir()]
        if "data.json" not in [i.filename for i in files]:
            raise ValueError("this zip has no data.json: it isn't a Sanguity export")
        if len(files) > MAX_ENTRIES or sum(i.file_size for i in files) > MAX_BYTES:
            raise ValueError(f"this zip holds more than {MAX_ENTRIES} files or {MAX_BYTES // 2**30} GB unpacked: "
                             "too big to be a Sanguity export")
        try:
            check_parts(json.loads(zf.read("data.json")))   # refused before anything is unpacked (#589)
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            raise ValueError("this zip's data.json isn't readable Sanguity data") from None
        sent = {}   # upload id in the zip -> its entries
        for i in files:
            parts = i.filename.split("/")
            if not ALLOWED.match(i.filename) or ".." in parts or "" in parts:
                raise ValueError(f"unexpected entry in the zip: {i.filename}")
            if (m := UPLOAD.match(i.filename)):
                sent.setdefault(int(m.group(1)), []).append((m.group(2), i))
                continue
            if i.filename in LEFT_OUT or i.filename.startswith("journal/"):   # a journal photo: restore() keeps it (#589)
                continue
            target = os.path.join(new, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if (w := WAVE.match(i.filename)):   # an ECG's samples, beside the daily.json that lists it (watch_store.wave)
                try:
                    wave = ecg.unpack(ecg.pack(json.loads(zf.read(i))))
                except (ValueError, TypeError, AttributeError):
                    wave = None
                if wave:
                    crypto.write_file(target + ".gz", ecg.pack(wave))
                continue
            crypto.write_file(target, zf.read(i))   # kept encrypted in the profile's folder
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(new, dest)
        photos = {i.filename: i for i in files if i.filename.startswith("journal/photos/")}
        with photo_files(pid) as written:   # what people wrote and decided, with the records (#589)
            n = prof.import_dataset(db, pid, dest, then=lambda db, data: restore(
                db, pid, data, lambda name: zf.read(photos[name]) if name in photos else None, written))
        watch = os.path.join(dest, "watch", "daily.json")
        if os.path.isfile(watch):
            db.execute("UPDATE profiles SET watch_file=? WHERE id=? AND (watch_file IS NULL OR watch_file LIKE ?)",
                       (watch, pid, os.path.join(prof.profile_dir(pid), "%")))
        for _, entries in sorted(sent.items()):
            upload_id = uploads.create(db, pid, None)
            try:
                for rel, i in entries:
                    with zf.open(i) as src:
                        uploads.add_file(db, pid, upload_id, rel, src)
                uploads.finish(db, pid, upload_id, unzip=False)   # unpacked already: zips left in it stay zips
                cycle_tracking.strip_uploads(db, pid, upload_id, on=cycle_tracking.is_on(db, pid))   # (#555)
            except uploads.UploadError as e:
                uploads.discard(db, pid, upload_id)
                raise ValueError(f"an upload in the zip can't be kept: {e}") from None
    return n
