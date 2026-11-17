"""Export everything in a profile as one zip, and load such a zip into a profile.

The zip:
  README.txt          what this is and how to load it again
  data.json           the records, with each document link pointing at the copy in documents/, and the journal
                      (notes, symptoms and side effects people wrote, journal.py) as its "journal" list
  settings.json       the settings set for the profile, shaped like settings.example.json (none: all at their defaults)
  documents/<id>/...  every document, and for HTML records the pictures and stylesheets they show
  watch/daily.json    the watch and scale summary shown with the profile (if any): its daily measures, home blood
                      pressure and temperature readings, glucose, workouts, ECGs and irregular rhythm notifications
  watch/ecg/<16 hex>.json  each ECG recording's samples (#182), the file the summary's ECG of that key draws (#225):
                      the person's data like the rest; loading the export keeps them beside its daily.json again
  uploads/<id>/...    the files uploaded on the import page, as they were (zips unpacked), one folder per upload
  activity.json       the profile's activity log
  cycle/cycle.json    menstrual cycle records (#183), only when the person exporting ticked them in, each time
                      (cycle_tracking.for_export); loading an export leaves them out, as cycle tracking is per install
It's written as it's sent, a piece at a time, so a large profile never sits in memory or on disk.
"""
import datetime, gzip, json, os, re, shutil, urllib.parse, zipfile

from . import crypto, cycle_tracking, ecg, profiles as prof, records, settings, uploads, watch_store

DOC_URL = re.compile(r"^api/profiles/\d+/documents/([^/#?]+)")
REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#?]+)""", re.I)
STORED = ("pdf", "jpg", "jpeg", "png", "gif", "webp", "zip", "gz")   # already compressed


def plan(db, pid, cycle=False):
    """Everything the export needs, gathered now: the stream runs after the request's database connection closes.
    cycle: the cycle records too, ticked in for this export (#183)."""
    p = db.execute("SELECT name, watch_file FROM profiles WHERE id=?", (pid,)).fetchone()
    docs = {r["id"]: (crypto.dec(r["path"]), r["ext"]) for r in db.execute("SELECT id, path, ext FROM documents WHERE profile_id=?", (pid,))}
    log = [dict(r) for r in db.execute("SELECT at, actor, action, detail, ip FROM activity WHERE profile_id=? ORDER BY id", (pid,))]
    for e in log:
        e["detail"] = json.loads(crypto.dec(e["detail"])) if e["detail"] else {}
        e["detail"].pop("key", None)
    watch = watch_store.load(db, pid)   # the merged summary (receiver + imports), or None
    waves = {}   # ECG samples (#225): file name -> where they are, for each recording the summary lists
    for e in (watch or {}).get("ecg") or []:
        name = ecg.file_name(e.get("key")) if isinstance(e, dict) else None
        path = watch_store.wave_path(db, pid, name[:-8]) if name else None
        if path:
            waves[name[:-8]] = path
    return {"pid": pid, "name": p["name"], "docs": docs, "activity": log,
            "data": records.assemble(db, pid), "uploads": uploads.stored_files(db, pid),
            "settings": settings.to_file(db, pid), "watch": watch, "waves": waves,
            "cycle": cycle_tracking.for_export(db, pid) if cycle else None}


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
    yield add_bytes("activity.json", json.dumps(p["activity"], indent=1).encode())
    readme = f"""Sanguity export: {p['name']}
Exported {now:%Y-%m-%d %H:%M} by {exported_by}.

This file holds someone's health records. Keep it somewhere private, ideally encrypted, and delete copies you
no longer need.

  data.json          the records ({len(included)} documents linked), and the journal ("journal": {len(data.get('journal') or []) if p['data'] else 0} entries)
  settings.json      the profile's settings{'' if len(p['settings']) > 1 else ' (none set: all at their defaults)'}
  documents/         the documents, one folder each
  watch/daily.json   watch and scale summary{'' if p['watch'] else ' (none)'}
{f"  watch/ecg/         each ECG recording's samples, by the key in daily.json: {len(p['waves'])}" + chr(10) if p.get('waves') else ''}{"  cycle/cycle.json   menstrual cycle records from the phone apps, ticked in for this export" + chr(10) if p.get('cycle') else ''}  uploads/           the files uploaded to import, as they were sent (zips unpacked): {len({u for u, _, _ in p['uploads']})} upload(s)
  activity.json      who opened, shared or changed this profile, and when
{f'''
{len(missing)} document link(s) pointed at files that are no longer on the server and are left out.
''' if missing else ''}
To load it into a Sanguity install (into an empty profile, or replacing one's records):
  python -m server.manage create-profile "Name" USERNAME      (prints the new profile's number)
  python -m server.manage import PROFILE_NUMBER /path/to/this.zip
"""
    yield add_bytes("README.txt", readme.encode())
    zf.close()
    yield sink.take()


# ---------------------------------------------------------------- loading an export

ALLOWED = re.compile(r"^(data\.json|settings\.json|README\.txt|activity\.json|watch/daily\.json|watch/ecg/[0-9a-f]{16}\.json|"
                     r"cycle/cycle\.json|documents/[^/]+/.+|uploads/\d+/.+)$")
WAVE = re.compile(r"^watch/ecg/([0-9a-f]{16})\.json$")   # (#225) kept as the app keeps them: gzipped, then sealed
LEFT_OUT = {"cycle/cycle.json"}   # cycle records (#183): not loaded into the profile, nor kept in its folder
UPLOAD = re.compile(r"^uploads/(\d+)/(.+)$")
# a zip that unpacks to more than this is refused before anything is written (a damaged or hostile archive could
# otherwise fill the disk); far above any real profile's records
MAX_ENTRIES, MAX_BYTES = 100_000, 16 * 2**30


def import_archive(db, pid, archive):
    """Unpack an export into the profile's own folder (so its documents stay) and load it. Returns the number of
    documents. Its uploads become the profile's uploads again, ready, as they were. The activity log in the zip is
    history from the other install and isn't merged in."""
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
        sent = {}   # upload id in the zip -> its entries
        for i in files:
            parts = i.filename.split("/")
            if not ALLOWED.match(i.filename) or ".." in parts or "" in parts:
                raise ValueError(f"unexpected entry in the zip: {i.filename}")
            if (m := UPLOAD.match(i.filename)):
                sent.setdefault(int(m.group(1)), []).append((m.group(2), i))
                continue
            if i.filename in LEFT_OUT:
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
        n = prof.import_dataset(db, pid, dest)
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
            except uploads.UploadError as e:
                uploads.discard(db, pid, upload_id)
                raise ValueError(f"an upload in the zip can't be kept: {e}") from None
    return n
