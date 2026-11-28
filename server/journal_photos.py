"""Photos attached to journal entries (#161): what a rash or a swelling looked like, in the person's own journal.

A photo is sent on its own first (PUT /api/profiles/{pid}/journal/photos, the request body is the picture), then named
by the entry it belongs to when that entry is saved (journal.py, "photos": [{"id", "alt"}]). Until then it is
"pending": a pending photo older than PENDING_SECONDS was abandoned (the form was closed) and is removed the next time
someone sends one or saves an entry, and when the app starts (sweep_all, #591, with any file no photo names). A photo
goes with its entry: deleting the entry, or saving it without the photo, deletes it, its file once that's saved (never
before: a save that fails keeps the photo whole, #591).

Kept like the profile's documents: in profiles/<id>/journal/, sealed (crypto.write_file) under a random name, so the
folder says nothing about whose pictures these are; the journal_photos table (schema version 32) holds only that name,
the picture's type and size, and the entry it belongs to. What the photo shows, in the person's words (alt), is in the
entry's own encrypted body. Served only to people who can view the profile (routes_journal.py), in "Export everything"
(journal/photos/), in backups with the profile's folder, and through a share link only when its owner ticked
"Photos in the journal" (share_links.py). Undoing or replacing an import never touches them.

Spots (#1139): a photo can be of a named spot ("Left forearm mole"; journal_spots, schema version 50, its name
encrypted), so the page can show one place over time, side by side. Tagged as its entry is saved ("photos": [{"id",
"alt", "spot": its name}]: an earlier spot's, in any letter case, or a new one), and renamed, merged into another or
taken off a photo on its own (rename_spot, merge_spots, untag). A spot no photo is of any more goes (tidy_spots). The
records list each photo's spot ({"id", "name"}), so "Export everything" carries it by name and loading the export
tags its photos again; backups have the table with the rest of the database. Nothing is worked out from the pictures.

Checked and cleaned before anything is stored:
  - only JPEG, PNG and WebP pictures, by what the file is, never by its name; anything else, or a file that isn't a
    whole picture of its type, is refused. HEIC (iPhone) is refused with a word on how to send a JPEG instead.
  - at most max_bytes() (JOURNAL_PHOTO_MAX_MB), MAX_PER_ENTRY to an entry, MAX_PENDING waiting at once per profile.
  - metadata removed (strip()), most importantly where it was taken:
      JPEG  every EXIF block (GPS location, camera make, model and serial number, date and time taken, the embedded
            thumbnail, maker notes), XMP (which can repeat the location), IPTC and Photoshop blocks, comments, every
            other application block but JFIF, the colour profile (ICC) and Adobe's colour flag, and anything after the
            picture's end (pictures a phone appends, such as depth maps, with their own EXIF). The one thing kept is
            which way up the picture is (EXIF orientation), written back as a new EXIF block holding nothing else.
      PNG   text chunks (tEXt, zTXt, iTXt: XMP, comments, "Raw profile" EXIF), eXIf, tIME and every chunk not needed to
            draw the picture; kept: the picture itself, its palette and transparency, colour space (gAMA, cHRM, sRGB,
            iCCP, cICP and the HDR chunks), pixel size (pHYs) and animation.
      WebP  the EXIF and XMP chunks and every chunk not needed to draw it; the header's flags say so.
    Pictures are never decoded or re-encoded here (no image library): only these blocks are taken out, so the pixels
    are exactly as sent.
"""
import os, secrets, struct

from . import config, crypto, db as dbm

TYPES = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
MAX_PER_ENTRY, MAX_PENDING, ALT_MAX, SPOT_MAX = 6, 30, 300, 80
PENDING_SECONDS = 86400


def max_bytes():
    """JOURNAL_PHOTO_MAX_MB, read at each photo (#998: a change applies at once)."""
    return config.setting("JOURNAL_PHOTO_MAX_MB") * config.MB


REMOVED = {"location": "where it was taken (GPS location)", "camera": "the camera or phone it was taken with",
           "time": "when it was taken", "other": "other details saved in the file"}


class PhotoError(ValueError):
    """Refused, with a message for the person and an HTTP status."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


NOT_A_PHOTO = "That file isn't a picture Sanguity can keep: attach a JPEG, PNG or WebP photo."


def too_big():
    return PhotoError(f"That photo is too big: a photo can be at most {max_bytes() // config.MB} MB.", 413)


# ---------------------------------------------------------------- what kind of picture

def kind_of(data):
    """"jpeg", "png" or "webp" by the file's first bytes, or PhotoError saying what to send instead."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"avif"):
        raise PhotoError("HEIC and AVIF photos can't be shown in every browser. Send it as a JPEG: on an iPhone, choose "
                         "the photo from the browser's own picker (it sends a JPEG), or set Camera → Formats to Most Compatible.")
    if data[:4] == b"GIF8":
        raise PhotoError("A GIF isn't kept as a photo: attach a JPEG, PNG or WebP picture.")
    raise PhotoError(NOT_A_PHOTO)


# ---------------------------------------------------------------- taking the metadata out

def _exif_found(tiff):
    """What an EXIF block (its TIFF data) holds, as REMOVED keys, and its orientation (1-8, or None)."""
    found, orientation = {"other"}, None
    if len(tiff) < 8 or tiff[:4] not in (b"II*\x00", b"MM\x00*"):
        return found, None
    e = "<" if tiff[:2] == b"II" else ">"

    def entries(off):
        if off + 2 > len(tiff):
            return
        n = struct.unpack(e + "H", tiff[off:off + 2])[0]
        for i in range(min(n, 500)):
            p = off + 2 + 12 * i
            if p + 12 > len(tiff):
                return
            tag, typ, count = struct.unpack(e + "HHI", tiff[p:p + 8])
            yield tag, typ, count, tiff[p + 8:p + 12]

    exif_ifd = None
    for tag, typ, count, val in entries(struct.unpack(e + "I", tiff[4:8])[0]):
        if tag == 0x0112 and typ == 3:
            v = struct.unpack(e + "H", val[:2])[0]
            orientation = v if 1 <= v <= 8 else None
        elif tag == 0x8825:
            found.add("location")
        elif tag in (0x010F, 0x0110, 0x0131):   # make, model, software
            found.add("camera")
        elif tag == 0x0132:                     # DateTime
            found.add("time")
        elif tag == 0x8769 and typ in (4, 13):
            exif_ifd = struct.unpack(e + "I", val)[0]
    if exif_ifd:
        for tag, *_ in entries(exif_ifd):
            if tag in (0x9003, 0x9004, 0x9010, 0x9011):   # when it was taken, and its zone
                found.add("time")
            elif tag in (0xA431, 0xA433, 0xA434, 0xA435, 0x927C):   # serial numbers, lens, maker notes
                found.add("camera")
    return found, orientation


def _orientation_exif(v):
    """An APP1 EXIF block holding only the orientation."""
    tiff = b"MM\x00*" + struct.pack(">I", 8) + struct.pack(">H", 1) + struct.pack(">HHIHH", 0x0112, 3, 1, v, 0) + b"\x00" * 4
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def _jpeg(data):
    out, found, orientation, i, n = [b"\xff\xd8"], set(), None, 2, len(data)
    while True:
        while i + 1 < n and data[i] == 0xFF and data[i + 1] == 0xFF:   # fill bytes
            i += 1
        if i + 2 > n or data[i] != 0xFF:
            raise PhotoError(NOT_A_PHOTO)
        m = data[i + 1]
        if m == 0xD9:   # the end of the picture: nothing after it is kept
            if not any(s[1] == 0xDA for s in out[1:]):
                raise PhotoError(NOT_A_PHOTO)
            if orientation and orientation != 1:
                out.insert(1, _orientation_exif(orientation))
            if data[i + 2:].strip(b"\x00"):
                found.add("other")
            return b"".join(out) + b"\xff\xd9", found
        if 0xD0 <= m <= 0xD7 or m in (0x01, 0xD8) or i + 4 > n:   # a marker with no length where a block should be
            raise PhotoError(NOT_A_PHOTO)
        size = struct.unpack(">H", data[i + 2:i + 4])[0]
        end = i + 2 + size
        if size < 2 or end > n:
            raise PhotoError(NOT_A_PHOTO)
        seg, body = data[i:end], data[i + 4:end]
        if m == 0xDA:   # start of scan: its header, then the coded picture up to the next marker
            j = end
            while True:
                j = data.find(b"\xff", j)
                if j < 0 or j + 1 >= n:
                    raise PhotoError(NOT_A_PHOTO)
                if data[j + 1] == 0x00 or 0xD0 <= data[j + 1] <= 0xD7 or data[j + 1] == 0xFF:
                    j += 1 if data[j + 1] == 0xFF else 2
                    continue
                break
            out.append(data[i:j])
            i = j
            continue
        if m == 0xE1 and body.startswith(b"Exif\x00"):
            f, o = _exif_found(body[6:])
            found |= f
            orientation = orientation or o
        elif m == 0xE1 and b"ns.adobe.com/xap" in body[:40]:
            found.add("other")
            if b"GPS" in body:
                found.add("location")
        elif m == 0xE0 and body[:5] in (b"JFIF\x00", b"JFXX\x00"):
            out.append(seg)
        elif m == 0xE2 and body.startswith(b"ICC_PROFILE\x00"):
            out.append(seg)
        elif m == 0xEE and body.startswith(b"Adobe"):
            out.append(seg)
        elif 0xE0 <= m <= 0xEF or m == 0xFE:   # every other application block, and comments
            found.add("other")
        else:   # tables, the frame header, restart interval...: what draws the picture
            out.append(seg)
        i = end


PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT", b"pHYs", b"bKGD",
            b"cICP", b"mDCv", b"cLLi", b"acTL", b"fcTL", b"fdAT"}


def _png(data):
    out, found, i, n, first = [data[:8]], set(), 8, len(data), True
    while True:
        if i + 12 > n:
            raise PhotoError(NOT_A_PHOTO)
        size, typ = struct.unpack(">I", data[i:i + 4])[0], data[i + 4:i + 8]
        end = i + 12 + size
        if end > n or (first and typ != b"IHDR"):
            raise PhotoError(NOT_A_PHOTO)
        first = False
        if typ in PNG_KEEP:
            out.append(data[i:end])
        else:
            found.add("location" if typ in (b"eXIf", b"iTXt", b"zTXt", b"tEXt") and b"GPS" in data[i + 8:end] else
                      "time" if typ == b"tIME" else "other")
            if typ == b"eXIf":
                found |= _exif_found(data[i + 8:end - 4])[0]
        if typ == b"IEND":
            if end < n and data[end:].strip(b"\x00"):
                found.add("other")
            return b"".join(out), found
        i = end


WEBP_KEEP = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF", b"ICCP"}


def _webp(data):
    size = struct.unpack("<I", data[4:8])[0]
    if size + 8 > len(data) or size < 4:
        raise PhotoError(NOT_A_PHOTO)
    chunks, found, i, end, drawn = [], set(), 12, size + 8, False
    while i < end:
        if i + 8 > end:
            raise PhotoError(NOT_A_PHOTO)
        typ, n = data[i:i + 4], struct.unpack("<I", data[i + 4:i + 8])[0]
        nxt = i + 8 + n + (n & 1)
        if i + 8 + n > end:
            raise PhotoError(NOT_A_PHOTO)
        if typ in WEBP_KEEP:
            c = bytearray(data[i:min(nxt, end)])
            if typ == b"VP8X" and n >= 1:
                c[8] &= ~0x0C & 0xFF   # the header's flags: no EXIF, no XMP any more
            chunks.append(bytes(c))
            drawn = drawn or typ in (b"VP8 ", b"VP8L", b"ANMF")
        elif typ == b"EXIF":
            found |= _exif_found(data[i + 8:i + 8 + n])[0]
        else:
            found.add("location" if typ == b"XMP " and b"GPS" in data[i + 8:i + 8 + n] else "other")
        i = nxt
    if not drawn:
        raise PhotoError(NOT_A_PHOTO)
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body, found


def strip(data):
    """(the picture without its metadata, its kind, what was taken out as REMOVED keys in their order), or PhotoError."""
    kind = kind_of(data)
    try:
        clean, found = {"jpeg": _jpeg, "png": _png, "webp": _webp}[kind](data)
    except (struct.error, IndexError):
        raise PhotoError(NOT_A_PHOTO) from None
    return clean, kind, [k for k in REMOVED if k in found]


# ---------------------------------------------------------------- stored

def folder(pid):
    return os.path.join(config.DATA_DIR, "profiles", str(int(pid)), "journal")   # profiles.profile_dir(pid)/journal


def _path(pid, file):
    return os.path.join(folder(pid), file)


def _sweep(db, pid):
    """Remove the profile's pending photos older than PENDING_SECONDS: their form was closed without saving."""
    for r in db.execute("SELECT id, file FROM journal_photos WHERE profile_id=? AND entry_id IS NULL AND created < ?",
                        (pid, dbm.now() - PENDING_SECONDS)).fetchall():
        _delete(db, pid, r["id"], r["file"])


def sweep_all(db):
    """At start-up (#591): every profile's pending photos older than PENDING_SECONDS, however long since one was last
    sent, and any file in a journal folder no photo names (a stop between saving a change and removing its file, or
    between writing a photo and recording it). Nothing else runs then. Returns how many files went."""
    base, n = os.path.join(config.DATA_DIR, "profiles"), 0
    for r in db.execute("SELECT id, profile_id, file FROM journal_photos WHERE entry_id IS NULL AND created < ?",
                        (dbm.now() - PENDING_SECONDS,)).fetchall():
        _delete(db, r["profile_id"], r["id"], r["file"])
        n += 1
    named = {(r["profile_id"], r["file"]) for r in db.execute("SELECT profile_id, file FROM journal_photos")}
    for p in os.listdir(base) if os.path.isdir(base) else ():
        d = folder(p) if p.isdigit() else None
        for f in os.listdir(d) if d and os.path.isdir(d) else ():
            if (int(p), f) not in named and os.path.isfile(os.path.join(d, f)):
                _unlink(p, f)
                n += 1
    return n


def _delete(db, pid, photo_id, file):
    """A photo's row, and its file once that's committed (dbm.after_commit; inside another kind of transaction, left
    for sweep_all to find)."""
    db.execute("DELETE FROM journal_photos WHERE id=?", (photo_id,))
    dbm.after_commit(db, lambda: _unlink(pid, file))


def _unlink(pid, file):
    try:
        os.remove(_path(pid, file))
    except FileNotFoundError:
        pass


def add(db, pid, user_id, data):
    """Check, clean and store a photo for an entry not saved yet. Returns {"id", "type", "bytes", "removed"}."""
    if len(data) > max_bytes():
        raise too_big()
    if not data:
        raise PhotoError("That file is empty.")
    clean, kind, removed = strip(data)
    _sweep(db, pid)
    if db.execute("SELECT COUNT(*) FROM journal_photos WHERE profile_id=? AND entry_id IS NULL", (pid,)).fetchone()[0] >= MAX_PENDING:
        raise PhotoError("Too many photos are waiting for their entry to be saved. Save or cancel that entry first.", 409)
    os.makedirs(folder(pid), mode=0o700, exist_ok=True)
    file = secrets.token_hex(16) + ".img"
    crypto.write_file(_path(pid, file), clean)
    try:
        pid_ = db.execute("INSERT INTO journal_photos(profile_id, file, type, bytes, created, created_by) VALUES(?, ?, ?, ?, ?, ?)",
                          (pid, file, kind, len(clean), dbm.now(), user_id)).lastrowid
    except Exception:
        _unlink(pid, file)
        raise
    return {"id": pid_, "type": kind, "bytes": len(clean), "removed": [REMOVED[k] for k in removed]}


def check(db, pid, photos, jid=None):
    """The entry's photos as stored in its body ([{"id", "alt"}]), or PhotoError: each one this profile's, pending or
    already this entry's (jid), once each."""
    if photos in (None, []):
        return []
    if not isinstance(photos, list):
        raise PhotoError("The photos must be a list.")
    if len(photos) > MAX_PER_ENTRY:
        raise PhotoError(f"An entry can have at most {MAX_PER_ENTRY} photos.")
    out, seen = [], set()
    for p in photos:
        pid_ = p.get("id") if isinstance(p, dict) else None
        alt = p.get("alt") if isinstance(p, dict) else None
        if isinstance(pid_, bool) or not isinstance(pid_, int) or pid_ in seen:
            raise PhotoError("A photo is listed twice, or isn't one that was sent.")
        alt = " ".join(alt.split()) if isinstance(alt, str) else ""
        if len(alt) > ALT_MAX:
            raise PhotoError(f"A photo's description is too long (at most {ALT_MAX} characters).")
        r = db.execute("SELECT entry_id FROM journal_photos WHERE id=? AND profile_id=?", (pid_, pid)).fetchone()
        if not r or (r["entry_id"] is not None and r["entry_id"] != jid):
            raise PhotoError("A photo isn't here any more: attach it again.")
        seen.add(pid_)
        spot = spot_name(p.get("spot"))
        out.append({"id": pid_, "alt": alt, **({"spot": spot} if spot else {})})
    return out


def kept(photos):
    """The photos as the entry's body keeps them, [{"id", "alt"}]: which spot each is of is its own row's (spot_id)."""
    return [{"id": p["id"], "alt": p["alt"]} for p in photos]


def attach(db, pid, jid, photos, user_id=None):
    """Make the entry's photos exactly these (call inside a transaction, after check()), each of the spot it names or
    of none (#1139); the ones it no longer lists are deleted, their files once it commits (#591: a save that fails
    keeps them); and abandoned ones are swept, as are spots no photo is of any more."""
    keep = [p["id"] for p in photos]
    for r in db.execute("SELECT id, file FROM journal_photos WHERE profile_id=? AND entry_id=?", (pid, jid)).fetchall():
        if r["id"] not in keep:
            _delete(db, pid, r["id"], r["file"])
    _sweep(db, pid)
    have = spots(db, pid) if any(p.get("spot") for p in photos) else {}
    for p in photos:
        sid = spot_named(db, pid, p["spot"], user_id, have) if p.get("spot") else None
        db.execute("UPDATE journal_photos SET entry_id=?, spot_id=? WHERE id=? AND profile_id=?", (jid, sid, p["id"], pid))
    tidy_spots(db, pid)


def drop_entry(db, pid, jid):
    """Delete an entry's photos (call inside the transaction that deletes the entry)."""
    attach(db, pid, jid, [])


# ---------------------------------------------------------------- spots (#1139)

def spot_name(v):
    """A spot's name as kept: its spaces evened out; "" for none. PhotoError when it isn't text or is too long."""
    if v is None:
        return ""
    if not isinstance(v, str):
        raise PhotoError("A spot's name must be text.")
    v = " ".join(v.split())
    if len(v) > SPOT_MAX:
        raise PhotoError(f"A spot's name is too long (at most {SPOT_MAX} characters).")
    return v


def spots(db, pid):
    """{spot id: its name} of the profile's spots."""
    return {r["id"]: crypto.dec(r["name"]) for r in db.execute("SELECT id, name FROM journal_spots WHERE profile_id=?", (pid,))}


def spot_named(db, pid, name, user_id=None, have=None):
    """The id of the profile's spot called name, in any letter case, made when there's none. have: spots(), kept up
    to date with one made here."""
    have = spots(db, pid) if have is None else have
    for i, n in have.items():
        if n.casefold() == name.casefold():
            return i
    i = db.execute("INSERT INTO journal_spots(profile_id, name, created, created_by) VALUES(?, ?, ?, ?)",
                   (pid, crypto.enc(name), dbm.now(), user_id)).lastrowid
    have[i] = name
    return i


def tidy_spots(db, pid):
    """Remove the profile's spots no photo is of any more (their last photo untagged, or deleted with its entry)."""
    db.execute("""DELETE FROM journal_spots WHERE profile_id=? AND id NOT IN
                  (SELECT spot_id FROM journal_photos WHERE profile_id=? AND spot_id IS NOT NULL)""", (pid, pid))


def _entries_of(db, pid, sid):
    return [r[0] for r in db.execute("""SELECT DISTINCT entry_id FROM journal_photos WHERE profile_id=? AND spot_id=?
                                        AND entry_id IS NOT NULL ORDER BY entry_id""", (pid, sid))]


def _spots_with(db, pid, *ids):
    """spots(), or PhotoError (404) when one of ids isn't the profile's spot (a page from before it was merged)."""
    have = spots(db, pid)
    if any(isinstance(i, bool) or not isinstance(i, int) or i not in have for i in ids):
        raise PhotoError("That spot isn't here any more: reload the page to see the spots as they are now.", 404)
    return have


def rename_spot(db, pid, sid, name):
    """Call a spot something else; never what another is called (merge the two instead). Returns the ids of the
    entries whose photos are of it."""
    have, name = _spots_with(db, pid, sid), spot_name(name)
    if not name:
        raise PhotoError("Give the spot a name.")
    other = next((n for i, n in have.items() if i != sid and n.casefold() == name.casefold()), None)
    if other is not None:
        raise PhotoError(f"There's a spot called “{other}” already: merge the two instead.", 409)
    db.execute("UPDATE journal_spots SET name=? WHERE id=? AND profile_id=?", (crypto.enc(name), sid, pid))
    return _entries_of(db, pid, sid)


def merge_spots(db, pid, sid, into):
    """Make every photo of spot sid one of spot into, which keeps its name; sid goes. Returns the ids of the entries
    whose photos changed."""
    _spots_with(db, pid, sid, into)
    if sid == into:
        raise PhotoError("Pick another spot to merge it with.")
    changed = _entries_of(db, pid, sid)
    db.execute("UPDATE journal_photos SET spot_id=? WHERE profile_id=? AND spot_id=?", (into, pid, sid))
    tidy_spots(db, pid)
    return changed


def untag(db, pid, photo_id):
    """Take a photo off its spot; it stays with its entry. Returns the entry's id, or PhotoError (404)."""
    r = db.execute("SELECT entry_id FROM journal_photos WHERE id=? AND profile_id=? AND entry_id IS NOT NULL AND spot_id IS NOT NULL",
                   (photo_id, pid)).fetchone()
    if not r:
        raise PhotoError("That photo isn't of a spot any more.", 404)
    db.execute("UPDATE journal_photos SET spot_id=NULL WHERE id=?", (photo_id,))
    tidy_spots(db, pid)
    return r["entry_id"]


def get(db, pid, photo_id, attached=True):
    """(the picture's bytes, its media type) or None. attached: only photos an entry lists (what viewers see)."""
    r = db.execute("SELECT file, type, entry_id FROM journal_photos WHERE id=? AND profile_id=?", (photo_id, pid)).fetchone()
    if not r or (attached and r["entry_id"] is None) or not os.path.isfile(_path(pid, r["file"])):
        return None
    return crypto.read_file(_path(pid, r["file"])), TYPES.get(r["type"], "application/octet-stream")


def info(db, pid):
    """{photo id: {"type", "bytes", "spot"?: {"id", "name"}}} of every photo an entry lists."""
    rows = db.execute("SELECT id, type, bytes, spot_id FROM journal_photos WHERE profile_id=? AND entry_id IS NOT NULL", (pid,)).fetchall()
    names = spots(db, pid) if any(r["spot_id"] for r in rows) else {}
    return {r["id"]: {"type": r["type"], "bytes": r["bytes"],
                      **({"spot": {"id": r["spot_id"], "name": names[r["spot_id"]]}} if r["spot_id"] in names else {})} for r in rows}


def stored(db, pid):
    """[(photo id, its type, its sealed file)] of every photo an entry lists, for Export everything."""
    return [(r["id"], r["type"], _path(pid, r["file"])) for r in
            db.execute("SELECT id, type, file FROM journal_photos WHERE profile_id=? AND entry_id IS NOT NULL ORDER BY id", (pid,))]


def any_attached(db, pid):
    return bool(db.execute("SELECT 1 FROM journal_photos WHERE profile_id=? AND entry_id IS NOT NULL LIMIT 1", (pid,)).fetchone())


def export_name(photo_id, kind):
    return f"journal/photos/{int(photo_id)}.{'jpg' if kind == 'jpeg' else kind}"
