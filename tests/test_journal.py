#!/usr/bin/env python3
"""The journal (server/journal.py, server/routes_journal.py, issue #49): notes, symptoms and side effects added,
changed and deleted; what can't be saved; roles and the CSRF check; the activity log (the kind, never the words);
encryption at rest; the entries in the records the page loads, kept when an import is undone; and in "Export
everything". And #161: symptom groups (the setting, and a group logged in one go), where on the body, a day's mood
and energy, and photos (checked by type, their metadata taken out, sealed, served to viewers only, with their entry
or not at all, in the export, and in a share link only when ticked). Fictional data only.

  python3 tests/test_journal.py
"""
import base64, io, json, os, re, shutil, struct, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-journal-test-")
BUILD = os.path.join(TMP, "build")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=BUILD,
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  PASSWORD_MIN_LENGTH="15", SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_CORRECTIONS=os.path.join(TMP, "config", "corrections.json"))
for d in ("records", "keys", "build", "config"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, config, crypto, db as dbm, export, journal, journal_photos, main, records   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"robin": "harbour lantern pebble crow", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}

BUILT = {
    "built": "2026-11-01T00:00:00+00:00", "source": "fictional",
    "events": [
        {"id": 1, "t": "2026-02-10T09:15:00", "k": "visit", "title": "Outpatient visit", "sub": "Dr. Fictional"},
        {"id": 2, "t": "2026-03-01T08:00:00", "k": "med", "title": "Examplomab 10 mg", "sub": "Active"},
        {"id": 3, "t": "2026-04-02T08:00:00", "k": "rx", "title": "Fictorin 5 mg tablet", "sub": "Pharmacy fill"},
    ],
    "details": {"2": {"type": "med", "instructions": "Once daily", "status": "Active", "src": "Fictional clinic"}},
    "labs": [], "upcoming": [], "problems": [], "milestones": [], "costs": [],
}
SECRET = "Fictional throbbing behind the left eye"

# (#161) tiny fictional pictures, as a browser's canvas writes them (a pink patch with a red spot)
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAA"
    "ACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0"
    "AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAA"
    "AAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAM"
    "ZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAA0JCgsKCA0LCgsODg0PEyAVExISEyccHhcgLikxMC4pLSwzOko+MzZG"
    "NywtQFdBRkxOUlNSMj5aYVpQYEpRUk//2wBDAQ4ODhMREyYVFSZPNS01T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09P"
    "T0//wAARCAAIAAwDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAiEAABAgMJAAAAAAAAAAAAAAAAEhMBAxQCBREyQUJRYXH/xAAVAQEBAAAA"
    "AAAAAAAAAAAAAAAEBf/EABYRAQEBAAAAAAAAAAAAAAAAAAEAIv/aAAwDAQACEQMRAD8AqXrWvS6ZxGGznsoyls2HM6YK91ACVNcl/9k=")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAYAAADN5B7xAAAAkUlEQVR4AZSNPQuCUBSGX6zhEkFbEURTzU1BY3t/oR/X0E+o3VFwctZJBD82QeQOinLOdI4i"
    "4oX38vLc89zjZL9PtyQOxGlqi9T1EX7/HOrExAiUUHgByihGay2HOrFJoUpy+cZ9yNQGnpi5lLA9HUbjQ6aE/eOG3eWMlTEc6sTkL0pYbwyOzzuu7xeHOjEp"
    "9AAAAP//+kSG+wAAAAZJREFUAwB8M1DV/aRUjAAAAABJRU5ErkJggg==")
WEBP = base64.b64decode(
    "UklGRjYCAABXRUJQVlA4WAoAAAAgAAAACwAABwAASUNDUMgBAAAAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAA"
    "ACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0"
    "AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAA"
    "AAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAM"
    "ZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADZWUDggSAAAADACAJ0BKgwACAACgEIlqAJ0ugACdohgoHgAAP7rhX9v7A06s3Qe"
    "Pe3qdvD4Av+i2I1jp2BeAu3+FsTU+/gu/7pYbzMSkAAAAA==")
MAKE, PLACE = b"FictionalPhone", b"GPS 51.5007N fictional"


def tiff_with(orientation=6):
    """EXIF (big-endian TIFF): orientation, a camera make, a time taken, and a GPS block with a fictional place."""
    entries = [(0x010F, 2, len(MAKE) + 1, None), (0x0112, 3, 1, orientation), (0x0132, 2, 20, None), (0x8825, 4, 1, None)]
    n = len(entries)
    data_at = 8 + 2 + 12 * n + 4
    blobs, out = b"", []
    for tag, typ, count, v in entries:
        if tag == 0x010F:
            out.append(struct.pack(">HHII", tag, typ, count, data_at + len(blobs))); blobs += MAKE + b"\0"
        elif tag == 0x0132:
            out.append(struct.pack(">HHII", tag, typ, count, data_at + len(blobs))); blobs += b"2026:04:03 08:15:00\0"
        elif tag == 0x0112:
            out.append(struct.pack(">HHIHH", tag, typ, count, v, 0))
        else:
            gps_at = data_at + len(blobs)
            out.append(struct.pack(">HHII", tag, typ, count, gps_at))
            blobs += struct.pack(">H", 1) + struct.pack(">HHII", 0x0001, 2, 2, 0x4E000000) + b"\0\0\0\0" + PLACE
    return b"MM\x00*" + struct.pack(">I", 8) + struct.pack(">H", n) + b"".join(out) + b"\0\0\0\0" + blobs


def jpeg_with_metadata():
    """The JPEG with EXIF (place, camera, time, turned on its side), XMP naming the place, a comment, and a second
    picture after its end, as phones append."""
    def seg(m, body):
        return b"\xff" + bytes([m]) + struct.pack(">H", len(body) + 2) + body
    xmp = seg(0xE1, b"http://ns.adobe.com/xap/1.0/\0<x:xmpmeta><exif:GPSLatitude>51,30N</exif:GPSLatitude>" + PLACE + b"</x:xmpmeta>")
    return JPEG[:20] + seg(0xE1, b"Exif\0\0" + tiff_with()) + xmp + seg(0xFE, b"Fictional comment " + MAKE) + JPEG[20:] + JPEG


def png_with_metadata():
    def chunk(typ, data):
        import zlib
        return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    at = PNG.index(b"IDAT") - 4
    return PNG[:at] + chunk(b"tEXt", b"Comment\0" + PLACE) + chunk(b"eXIf", tiff_with(1)) + chunk(b"tIME", b"\x07\xea\x04\x03\x08\x0f\x00") + PNG[at:]


def webp_with_metadata():
    exif = tiff_with(1)
    body = WEBP[12:] + b"EXIF" + struct.pack("<I", len(exif)) + exif + (b"\0" if len(exif) & 1 else b"")
    vp8x = bytearray(body)
    vp8x[8] |= 0x08   # the header says there's EXIF
    body = b"WEBP" + bytes(vp8x)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def client():
    return TestClient(main.app, base_url="http://testserver")


class Journal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BUILD, "data.json"), "w", encoding="utf-8") as f:
            json.dump(BUILT, f)
        cls.ctx = client().__enter__()
        db = dbm.connect()
        uids = {n: accounts.create(db, n, password=pw)[0] for n, pw in PW.items()}
        cls.pid = db.execute("SELECT id FROM profiles WHERE linked_build=1").fetchone()[0]
        for who, role in (("robin", "owner"), ("lee", "editor"), ("kim", "viewer")):
            db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, ?)", (uids[who], cls.pid, role))
        db.close()
        cls.robin, cls.kim, cls.lee = (cls.login(n) for n in ("robin", "kim", "lee"))

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    @staticmethod
    def login(name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def setUp(self):
        db = dbm.connect()
        db.execute("DELETE FROM journal")
        db.execute("DELETE FROM journal_photos")
        db.execute("DELETE FROM settings WHERE key='journal.groups'")
        records._bump(db, self.pid)
        db.close()

    def url(self, rest=""):
        return f"/api/profiles/{self.pid}/journal{rest}"

    def add(self, c=None, status=201, **entry):
        r = (c or self.robin).post(self.url(), json=entry, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def data(self, c=None):
        r = (c or self.robin).get(f"/api/profiles/{self.pid}/data")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_add_change_delete(self):
        sid = self.add(kind="symptom", t="2026-04-03", title="Headache", severity=6, note=SECRET)["id"]
        nid = self.add(kind="note", t="2026-04-04T21:30", note="Slept badly, fictional")["id"]
        fid = self.add(kind="side_effect", t="2026-04-05T08:00", title="Nausea", severity=3, med="Fictorin 5 mg tablet")["id"]
        listed = self.robin.get(self.url()).json()
        self.assertEqual(listed["medications"], ["Examplomab 10 mg", "Fictorin 5 mg tablet"])
        got = {e["id"]: e for e in listed["entries"]}
        self.assertEqual([e["id"] for e in listed["entries"]], [sid, nid, fid], "oldest first, by date")
        self.assertEqual({k: got[sid][k] for k in ("kind", "t", "title", "severity", "note", "med", "by")},
                         {"kind": "symptom", "t": "2026-04-03", "title": "Headache", "severity": 6, "note": SECRET, "med": None, "by": "robin"})
        self.assertEqual((got[nid]["kind"], got[nid]["severity"], got[nid]["t"]), ("note", None, "2026-04-04T21:30"))
        self.assertEqual(got[fid]["med"], "Fictorin 5 mg tablet")

        r = self.lee.put(self.url(f"/{sid}"), json={"kind": "symptom", "t": "2026-04-03T07:00", "title": "Headache", "severity": 8}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        e = {x["id"]: x for x in self.robin.get(self.url()).json()["entries"]}[sid]
        self.assertEqual((e["severity"], e["t"], e["note"], e["by"], e["changed_by"]), (8, "2026-04-03T07:00", "", "robin", "lee"))
        self.assertTrue(e["changed"])

        self.assertEqual(self.robin.delete(self.url(f"/{nid}"), headers=H).status_code, 200)
        self.assertEqual(self.robin.delete(self.url(f"/{nid}"), headers=H).status_code, 404)
        self.assertEqual(self.robin.put(self.url(f"/{nid}"), json={"kind": "note", "t": "2026-04-04", "note": "x"}, headers=H).status_code, 404)
        self.assertEqual([x["id"] for x in self.robin.get(self.url()).json()["entries"]], [sid, fid])

    def test_replies_carry_what_changed(self):
        """#271: each change's reply has the entries it changed as the records list them, so the page updates in place"""
        j = self.add(kind="symptom", t="2026-04-03", title="Headache", severity=6, where=["head"])
        listed = lambda: {e["id"]: e for e in self.data()["journal"]}
        self.assertEqual(j["journal"], [listed()[j["id"]]])
        r = self.lee.put(self.url(f"/{j['id']}"), json={"kind": "symptom", "t": "2026-04-03T07:00", "title": "Headache", "severity": 8}, headers=H)
        self.assertEqual(r.json()["journal"], [listed()[j["id"]]])
        self.assertEqual(r.json()["journal"][0]["changed_by"], "lee")
        r = self.robin.delete(self.url(f"/{j['id']}"), headers=H)
        self.assertEqual(r.json(), {"ok": True, "removed": [j["id"]]})

    def test_replies_say_the_records_revision(self):
        """#405: each change's reply says the records' revision after it, as the page's data does, so the offline copy
        can follow the change"""
        rev = lambda: self.robin.get(f"/api/profiles/{self.pid}/data").headers["X-Records-Rev"]
        before = rev()
        r = self.robin.post(self.url(), json={"kind": "note", "t": "2026-04-03", "note": "Fictional"}, headers=H)
        self.assertEqual(r.headers["X-Records-Rev"], rev())
        self.assertNotEqual(r.headers["X-Records-Rev"], before)
        jid = r.json()["id"]
        for change in (lambda: self.robin.post(self.url("/group"), json={"group": "Fictional", "t": "2026-04-03", "items": [{"title": "Cough", "severity": 2}]}, headers=H),
                       lambda: self.robin.put(self.url(f"/{jid}"), json={"kind": "note", "t": "2026-04-04", "note": "Fictional, changed"}, headers=H),
                       lambda: self.robin.delete(self.url(f"/{jid}"), headers=H)):
            r = change()
            self.assertIn(r.status_code, (200, 201), r.text)
            self.assertEqual(r.headers["X-Records-Rev"], rev())

    def test_a_journal_with_nothing_imported(self):
        """#472: a profile with journal entries but no records has its journal as its records' data (no records but
        the journal), so the records page shows it and the palette finds it; with none, it has no records yet (204)"""
        r = self.robin.post("/api/profiles", json={"name": "Journal only, fictional"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["id"]
        data = lambda: self.robin.get(f"/api/profiles/{pid}/data")
        self.assertEqual(data().status_code, 204)
        r = self.robin.post(f"/api/profiles/{pid}/journal", json={"kind": "note", "t": "2026-04-03", "note": "Fictional"}, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        got = data()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.headers["X-Records-Rev"], r.headers["X-Records-Rev"])
        d = got.json()
        self.assertEqual((d["events"], d["labs"], d["imports"], [e["note"] for e in d["journal"]]), ([], [], {}, ["Fictional"]))
        self.assertEqual(self.kim.get(f"/api/profiles/{pid}/data").status_code, 404)   # still only for its members
        self.robin.delete(f"/api/profiles/{pid}/journal/{r.json()['id']}", headers=H)
        self.assertEqual(data().status_code, 204)

    def test_a_watch_with_nothing_imported(self):
        """#477: a profile with a phone connected and nothing imported, no journal either, has the same data (no
        records, no entries), so the records page opens on Body and activity rather than "no records yet"; while the
        phone has sent nothing, it has no records yet (204)"""
        r = self.robin.post("/api/profiles", json={"name": "Watch only, fictional"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["id"]
        wf = os.path.join(TMP, "watch477", "daily.json")
        db = dbm.connect()
        db.execute("UPDATE profiles SET watch_file=? WHERE id=?", (wf, pid))
        db.close()
        data = lambda: self.robin.get(f"/api/profiles/{pid}/data")
        self.assertEqual(data().status_code, 204)   # connected, nothing sent yet
        os.makedirs(os.path.dirname(wf), exist_ok=True)
        with open(wf, "w") as f:
            json.dump({"metrics": [{"key": "steps", "name": "Steps", "unit": "steps", "group": "activity", "pts": [["2026-04-03", 5000]]}]}, f)
        got = data()
        self.assertEqual(got.status_code, 200, got.text)
        d = got.json()
        self.assertEqual((d["events"], d["labs"], d["imports"], d["journal"]), ([], [], {}, []))
        self.assertEqual(self.robin.get(f"/api/profiles/{pid}/watch").status_code, 200)
        self.assertEqual(self.kim.get(f"/api/profiles/{pid}/data").status_code, 404)   # still only for its members
        os.remove(wf)
        self.assertEqual(data().status_code, 204)

    def test_what_cant_be_saved(self):
        bad = [
            {"kind": "mood", "t": "2026-04-03", "note": "x"},
            {"kind": "note", "t": "2026-04-03", "note": "   "},                                       # empty note
            {"kind": "note", "t": "2026-04-03", "note": "x", "severity": 4},                         # notes aren't rated
            {"kind": "symptom", "t": "2026-04-03", "title": "Cough"},                                # no severity
            {"kind": "symptom", "t": "2026-04-03", "title": "Cough", "severity": 11},
            {"kind": "symptom", "t": "2026-04-03", "title": "Cough", "severity": -1},
            {"kind": "symptom", "t": "2026-04-03", "severity": 2},                                   # what was it?
            {"kind": "symptom", "t": "03/04/2026", "title": "Cough", "severity": 2},
            {"kind": "symptom", "t": "2026-02-30", "title": "Cough", "severity": 2},
            {"kind": "side_effect", "t": "2026-04-03", "title": "Rash", "severity": 2, "med": "Not in the records"},
            {"kind": "note", "t": "2026-04-03", "note": "x" * 5001},
            {"kind": "note", "t": "2207-11-20", "note": "a year typed wrong"},                         # more than a year ahead (#479)
        ]
        for b in bad:
            r = self.robin.post(self.url(), json=b, headers=H)
            self.assertIn(r.status_code, (400, 422), b)
        self.assertEqual(self.robin.get(self.url()).json()["entries"], [])

    def test_roles_csrf_and_activity(self):
        jid = self.add(c=self.lee, kind="symptom", t="2026-04-03", title="Fatigue", severity=4, note=SECRET)["id"]   # editors may
        self.assertEqual(self.kim.get(self.url()).status_code, 200, "viewers read")
        self.assertEqual([e["title"] for e in self.data(self.kim)["journal"]], ["Fatigue"])
        self.add(c=self.kim, status=403, kind="note", t="2026-04-03", note="x")
        self.assertEqual(self.kim.put(self.url(f"/{jid}"), json={"kind": "note", "t": "2026-04-03", "note": "x"}, headers=H).status_code, 403)
        self.assertEqual(self.kim.delete(self.url(f"/{jid}"), headers=H).status_code, 403)
        self.assertEqual(self.robin.post(self.url(), json={"kind": "note", "t": "2026-04-03", "note": "x"}).status_code, 403, "CSRF: no X-Sanguity")
        self.assertEqual(self.robin.delete(self.url(f"/{jid}")).status_code, 403)
        self.assertEqual(client().get(self.url()).status_code, 401)
        self.assertEqual(self.robin.put(self.url(f"/{jid}"), json={"kind": "symptom", "t": "2026-04-03", "title": "Fatigue", "severity": 5}, headers=H).status_code, 200)
        self.assertEqual(self.robin.delete(self.url(f"/{jid}"), headers=H).status_code, 200)
        log = self.robin.get(f"/api/profiles/{self.pid}/activity?action=journal").json()
        entries = log["entries"] if isinstance(log, dict) else log
        self.assertEqual([(e["actor"], e["detail"]) for e in entries][:3],
                         [("robin", {"action": "delete", "kind": "symptom"}), ("robin", {"action": "change", "kind": "symptom"}),
                          ("lee", {"action": "add", "kind": "symptom"})])
        self.assertNotIn("Fatigue", json.dumps(entries))

    def test_encrypted_at_rest(self):
        self.add(kind="side_effect", t="2026-04-05", title="Fictional tingling", severity=7, note=SECRET, med="Examplomab 10 mg")
        db = dbm.connect()
        rows = db.execute("SELECT * FROM journal").fetchall()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["body"].startswith(crypto.FIELD))
        raw = json.dumps([tuple(r) for r in rows])
        for plain in (SECRET, "Fictional tingling", "Examplomab", "side_effect", "2026-04-05"):
            self.assertNotIn(plain, raw)
        for path in (config.DB_PATH, config.DB_PATH + "-wal"):
            if os.path.exists(path):
                with open(path, "rb") as f:
                    blob = f.read()
                self.assertNotIn(SECRET.encode(), blob, path)
                self.assertNotIn(b"Fictional tingling", blob, path)

    def test_in_the_records_and_kept_through_undo(self):
        self.add(kind="symptom", t="2026-04-03", title="Headache", severity=6)
        d = self.data()
        self.assertEqual([(e["kind"], e["title"], e["severity"]) for e in d["journal"]], [("symptom", "Headache", 6)])
        self.assertEqual(len(d["events"]), len(BUILT["events"]), "entries are their own list, not events")
        # an import added and undone: the journal stays
        db = dbm.connect()
        with dbm.transaction(db):
            iid = records.store(db, self.pid, {"events": [{"id": "a", "t": "2026-05-01T10:00:00", "k": "visit", "title": "Video visit",
                                                           "key": "fhir:Encounter/fict-9"}], "details": {}, "labs": [], "costs": []},
                                "upload", fmt="fhir")
        db.close()
        self.assertEqual(len(self.data()["events"]), len(BUILT["events"]) + 1)
        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, [iid])
        db.close()
        self.assertEqual([e["title"] for e in self.data()["journal"]], ["Headache"])
        # a stored import never takes a journal list in (an export's data.json has one)
        db = dbm.connect()
        with dbm.transaction(db):
            iid = records.store(db, self.pid, {"events": [], "journal": [{"kind": "note", "t": "2026-01-01", "note": "from a file"}]}, "upload")
            records.drop(db, self.pid, [iid])
        db.close()
        self.assertEqual(len(self.data()["journal"]), 1)

    def test_in_export(self):
        self.add(kind="symptom", t="2026-04-03", title="Headache", severity=6, note=SECRET)
        self.add(kind="note", t="2026-04-04", note="Fictional note for the export")
        r = self.lee.get(f"/api/profiles/{self.pid}/export")
        self.assertEqual(r.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            data = json.loads(z.read("data.json"))
            readme = z.read("README.txt").decode()
        self.assertEqual([(e["kind"], e.get("title"), e["note"]) for e in data["journal"]],
                         [("symptom", "Headache", SECRET), ("note", "", "Fictional note for the export")])
        self.assertIn('"journal": 2 entries', readme)
        self.assertEqual(self.kim.get(f"/api/profiles/{self.pid}/export").status_code, 403)

    def test_medication_kept_after_its_import_goes(self):
        db = dbm.connect()
        with dbm.transaction(db):
            iid = records.store(db, self.pid, {"events": [{"id": "m", "t": "2026-06-01T08:00:00", "k": "med", "title": "Fictazole 2 mg",
                                                           "key": "fhir:MedicationRequest/fict-1"}]}, "upload", fmt="fhir")
        db.close()
        jid = self.add(kind="side_effect", t="2026-06-02", title="Dizziness", severity=2, med="Fictazole 2 mg")["id"]
        db = dbm.connect()
        with dbm.transaction(db):
            records.drop(db, self.pid, [iid])
        db.close()
        self.assertNotIn("Fictazole 2 mg", self.robin.get(self.url()).json()["medications"])
        ok = {"kind": "side_effect", "t": "2026-06-02", "title": "Dizziness", "severity": 4, "med": "Fictazole 2 mg"}
        self.assertEqual(self.robin.put(self.url(f"/{jid}"), json=ok, headers=H).status_code, 200, "its own link is kept")
        self.add(status=400, **ok)   # but a new entry can't link to it

    def test_profile_deleted_takes_its_journal(self):
        db = dbm.connect()
        pid = db.execute("INSERT INTO profiles(name, created) VALUES('Temp (fictional)', 1)").lastrowid
        uid = db.execute("SELECT id FROM users WHERE username='robin'").fetchone()[0]
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'owner')", (uid, pid))
        db.execute("INSERT INTO journal(profile_id, body, created_name, created) VALUES(?, ?, 'robin', 1)", (pid, crypto.enc("{}")))
        db.execute("DELETE FROM profiles WHERE id=?", (pid,))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM journal WHERE profile_id=?", (pid,)).fetchone()[0], 0)
        db.close()

    # ---------------------------------------------------------------- #161

    def test_where_on_the_body(self):
        jid = self.add(kind="symptom", t="2026-04-03", title="Aching", severity=5, where=["r-knee", "lower-back", "r-knee"])["id"]
        e = {x["id"]: x for x in self.robin.get(self.url()).json()["entries"]}[jid]
        self.assertEqual(e["where"], ["lower-back", "r-knee"], "once each, in the outline's order")
        for bad in ({"where": ["elbow-ish"]}, {"where": "r-knee"}, {"where": list(journal.REGIONS)[:13]}, {"where": [3]}):
            r = self.robin.post(self.url(), json={"kind": "symptom", "t": "2026-04-03", "title": "Aching", "severity": 5, **bad}, headers=H)
            self.assertIn(r.status_code, (400, 422), bad)
        self.add(status=400, kind="note", t="2026-04-03", note="x", where=["head"])   # only a symptom or side effect
        self.add(kind="side_effect", t="2026-04-04", title="Rash", severity=3, where=["l-forearm"])
        self.assertEqual(len(journal.REGIONS), 44)
        self.assertEqual(journal.REGIONS["r-forearm-back"], "Back of the right forearm and elbow")

    def test_regions_are_the_ones_the_page_draws(self):
        with open(os.path.join(ROOT, "www", "js", "journal-body.js"), encoding="utf-8") as f:
            js = f.read()
        table = js[js.index("const JB_REGIONS = ["):]
        drawn = re.findall(r'\["([a-z-]+)",\s*"(?:f|b)"', table[:table.index("\n];")])
        self.assertEqual(sorted(drawn), sorted(journal.REGIONS), "every region drawn once, and only those")

    def test_mood_and_energy(self):
        mid = self.add(kind="mood", t="2026-04-03", mood=7, energy=4, note="Fictional good day")["id"]
        self.add(kind="mood", t="2026-04-04", energy=2)
        got = {e["t"]: e for e in self.robin.get(self.url()).json()["entries"]}
        self.assertEqual({k: got["2026-04-03"][k] for k in ("kind", "mood", "energy", "note", "severity", "title")},
                         {"kind": "mood", "mood": 7, "energy": 4, "note": "Fictional good day", "severity": None, "title": ""})
        self.assertEqual((got["2026-04-04"]["mood"], got["2026-04-04"]["energy"]), (None, 2))
        for bad in ({"t": "2026-04-05"}, {"t": "2026-04-05", "mood": 11}, {"t": "2026-04-05", "mood": True},
                    {"t": "2026-04-05T08:00", "mood": 5}, {"t": "2026-04-05", "mood": 5, "severity": 3},
                    {"t": "2026-04-05", "mood": 5, "title": "x"}, {"t": "2026-04-05", "mood": 5, "where": ["head"]}):
            r = self.robin.post(self.url(), json={"kind": "mood", **bad}, headers=H)
            self.assertIn(r.status_code, (400, 422), bad)
        r = self.add(status=400, kind="mood", t="2026-04-03", mood=2)
        self.assertIn("already", r["detail"], "one a day")
        self.add(status=400, kind="symptom", t="2026-04-05", title="Cough", severity=2, mood=3)
        r = self.robin.put(self.url(f"/{mid}"), json={"kind": "mood", "t": "2026-04-03", "mood": 8}, headers=H)
        self.assertEqual(r.status_code, 200, "its own day is fine")
        log = self.robin.get(f"/api/profiles/{self.pid}/activity?action=journal").json()
        entries = log["entries"] if isinstance(log, dict) else log
        self.assertEqual(entries[0]["detail"], {"action": "change", "kind": "mood"})

    def put_groups(self, groups, c=None, status=200):
        r = (c or self.robin).put(f"/api/profiles/{self.pid}/settings/journal.groups", json={"value": groups}, headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r

    def test_symptom_groups(self):
        self.put_groups([{"name": " Migraine  day ", "symptoms": ["Headache", "Nausea", "headache", "Light sensitivity", " "]}])
        got = self.robin.get(f"/api/profiles/{self.pid}/settings").json()["journal"]["groups"]
        self.assertEqual(got, [{"name": "Migraine day", "symptoms": ["Headache", "Nausea", "Light sensitivity"]}])
        self.assertEqual(self.kim.get(f"/api/profiles/{self.pid}/settings").json()["journal"]["groups"], got, "viewers see them")
        self.put_groups([], c=self.kim, status=403)
        for bad in ([{"name": "A", "symptoms": []}], [{"name": "", "symptoms": ["x"]}], [{"name": "A", "symptoms": ["x"]}, {"name": "a", "symptoms": ["y"]}],
                    [{"name": "A", "symptoms": [f"s{i}" for i in range(13)]}], "Migraine", [{"name": "A" * 81, "symptoms": ["x"]}]):
            self.put_groups(bad, status=400)
        db = dbm.connect()
        row = db.execute("SELECT value, sealed FROM settings WHERE key='journal.groups'").fetchone()
        db.close()
        self.assertTrue(row["sealed"].startswith(crypto.FIELD) and row["value"] is None and "Nausea" not in row["sealed"], "sealed at rest")
        # logged in one go: a symptom each, with its own severity, place and the group's name; all or nothing
        g = f"/api/profiles/{self.pid}/journal/group"
        body = {"group": "Migraine day", "t": "2026-04-03T07:30", "note": "Fictional, after a long screen day",
                "items": [{"title": "Headache", "severity": 7, "where": ["head"]}, {"title": "Nausea", "severity": 3}, {"title": "Light sensitivity", "severity": 5}]}
        bad = {**body, "items": body["items"][:2] + [{"title": "Light sensitivity", "severity": 12}]}
        self.assertEqual(self.robin.post(g, json=bad, headers=H).status_code, 400)
        self.assertEqual(self.robin.get(self.url()).json()["entries"], [], "nothing saved when one can't be")
        self.assertEqual(self.kim.post(g, json=body, headers=H).status_code, 403)
        self.assertEqual(self.robin.post(g, json=body).status_code, 403, "CSRF")
        r = self.lee.post(g, json=body, headers=H)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(len(r.json()["ids"]), 3)
        self.assertEqual(r.json()["journal"], self.robin.get(self.url()).json()["entries"], "the reply has the entries it added (#271)")
        got = [(e["kind"], e["title"], e["severity"], e.get("where"), e["group"], e["t"], e["note"]) for e in self.robin.get(self.url()).json()["entries"]]
        self.assertEqual(got, [("symptom", "Headache", 7, ["head"], "Migraine day", "2026-04-03T07:30", body["note"]),
                               ("symptom", "Nausea", 3, None, "Migraine day", "2026-04-03T07:30", body["note"]),
                               ("symptom", "Light sensitivity", 5, None, "Migraine day", "2026-04-03T07:30", body["note"])])
        self.put_groups([])   # the entries keep the name they were logged with
        self.assertEqual({e["group"] for e in self.robin.get(self.url()).json()["entries"]}, {"Migraine day"})
        for b in ({**body, "items": []}, {**body, "group": " "}, {**body, "items": ["Headache"]}):
            self.assertEqual(self.robin.post(g, json=b, headers=H).status_code, 400, b)

    def photo(self, data, c=None, status=201):
        r = (c or self.robin).put(f"/api/profiles/{self.pid}/journal/photos", content=data, headers={**H, "Content-Type": "application/octet-stream"})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_photo_metadata_taken_out(self):
        clean, kind, removed = journal_photos.strip(jpeg_with_metadata())
        self.assertEqual((kind, removed), ("jpeg", ["location", "camera", "time", "other"]))
        for gone in (MAKE, PLACE, b"GPS", b"ns.adobe.com", b"Fictional comment", b"2026:04:03"):
            self.assertNotIn(gone, clean)
        self.assertEqual(clean.count(b"\xff\xd9"), 1, "nothing after the picture's end")
        self.assertTrue(clean.startswith(b"\xff\xd8") and clean.endswith(b"\xff\xd9"))
        self.assertIn(b"ICC_PROFILE", clean, "the colour profile stays")
        self.assertIn(JPEG[JPEG.index(b"\xff\xda"):], clean, "the picture itself, byte for byte")
        at = clean.index(b"Exif\0\0")
        self.assertEqual(journal_photos._exif_found(clean[at + 6:at + 6 + 26]), ({"other"}, 6), "only which way up it is")
        self.assertEqual(journal_photos.strip(JPEG)[2], [], "a picture with nothing to take out")
        self.assertNotIn(b"Exif", journal_photos.strip(JPEG)[0])

        clean, kind, removed = journal_photos.strip(png_with_metadata())
        self.assertEqual((kind, removed), ("png", ["location", "camera", "time", "other"]))
        self.assertEqual(clean, PNG, "exactly the picture as the canvas wrote it")
        clean, kind, removed = journal_photos.strip(webp_with_metadata())
        self.assertEqual((kind, set(removed)), ("webp", {"location", "camera", "time", "other"}))
        self.assertNotIn(MAKE, clean)
        self.assertEqual(clean[20] & 0x0C, 0, "the header no longer says EXIF or XMP")
        self.assertEqual(struct.unpack("<I", clean[4:8])[0], len(clean) - 8)
        self.assertEqual(clean, WEBP)

        for bad, word in ((b"GIF89a" + b"\0" * 20, "GIF"), (b"\0\0\0\x18ftypheic" + b"\0" * 20, "HEIC"), (b"%PDF-1.7 fictional", "JPEG, PNG or WebP"),
                          (JPEG[:200], "JPEG, PNG or WebP"), (PNG[:40], "JPEG, PNG or WebP"), (WEBP[:30], "JPEG, PNG or WebP"),
                          (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "JPEG, PNG or WebP")):
            with self.assertRaises(journal_photos.PhotoError) as x:
                journal_photos.strip(bad)
            self.assertIn(word, str(x.exception), bad[:12])

    def test_photos(self):
        sent = self.photo(jpeg_with_metadata())
        self.assertEqual((sent["type"], sent["removed"][0]), ("jpeg", "where it was taken (GPS location)"))
        url = f"/api/profiles/{self.pid}/journal/photos/{sent['id']}"
        self.assertEqual(self.robin.get(url).status_code, 404, "not shown until an entry lists it")
        # stored sealed, under a random name; nothing about it in the database but its type and size
        db = dbm.connect()
        row = dict(db.execute("SELECT * FROM journal_photos WHERE id=?", (sent["id"],)).fetchone())
        db.close()
        path = os.path.join(config.DATA_DIR, "profiles", str(self.pid), "journal", row["file"])
        with open(path, "rb") as f:
            raw = f.read()
        self.assertTrue(crypto.file_is_encrypted(path))
        self.assertNotIn(JPEG[-60:], raw)
        self.assertRegex(row["file"], r"^[0-9a-f]{32}\.img$")
        self.assertEqual((row["entry_id"], row["type"]), (None, "jpeg"))
        # roles: editors send, viewers only see
        self.photo(PNG, c=self.kim, status=403)
        r = self.robin.put(f"/api/profiles/{self.pid}/journal/photos", content=PNG)
        self.assertEqual(r.status_code, 403, "CSRF")
        self.assertEqual(self.photo(b"GIF89a fictional", status=400)["detail"], "A GIF isn't kept as a photo: attach a JPEG, PNG or WebP picture.")
        old = journal_photos.MAX_BYTES
        journal_photos.MAX_BYTES = 500
        try:
            self.assertIn("too big", self.photo(JPEG, status=413)["detail"])
        finally:
            journal_photos.MAX_BYTES = old
        alt = "Fictional red patch on the left forearm"
        jid = self.add(kind="symptom", t="2026-04-03", title="Rash", severity=4, where=["l-forearm"],
                       photos=[{"id": sent["id"], "alt": alt}])["id"]
        e = self.robin.get(self.url()).json()["entries"][0]
        self.assertEqual(e["photos"], [{"id": sent["id"], "alt": alt, "type": "jpeg", "bytes": row["bytes"]}])
        r = self.kim.get(url)
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/jpeg"), "a viewer sees it")
        self.assertEqual(r.content, journal_photos.strip(jpeg_with_metadata())[0])
        self.assertIn("default-src 'none'", r.headers["content-security-policy"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertTrue(r.headers["content-disposition"].startswith("inline"))
        self.assertEqual(client().get(url).status_code, 401)
        db = dbm.connect()
        stranger = accounts.create(db, "ada", password="a stranger to every record")[0]
        db.close()
        ada = client()
        ada.post("/api/login", json={"username": "ada", "password": "a stranger to every record"}, headers=H)
        self.assertIn(ada.get(url).status_code, (403, 404))
        self.assertTrue(stranger)
        # a photo belongs to one entry
        self.add(status=400, kind="note", t="2026-04-04", note="x", photos=[{"id": sent["id"]}])
        self.add(status=400, kind="note", t="2026-04-04", note="x", photos=[{"id": 99999}])
        self.add(status=400, kind="mood", t="2026-04-04", mood=3, photos=[{"id": self.photo(PNG)["id"]}])
        many = [{"id": self.photo(PNG)["id"]} for _ in range(7)]
        self.add(status=400, kind="note", t="2026-04-04", note="x", photos=many)
        # kept when an import is undone
        db = dbm.connect()
        with dbm.transaction(db):
            iid = records.store(db, self.pid, {"events": [{"id": "a", "t": "2026-05-01T10:00:00", "k": "visit", "title": "Video visit"}]}, "upload")
        with dbm.transaction(db):
            records.drop(db, self.pid, [iid])
        db.close()
        self.assertEqual(self.kim.get(url).status_code, 200)
        # changed without it: the photo goes
        two = self.photo(WEBP)
        r = self.robin.put(self.url(f"/{jid}"), json={"kind": "symptom", "t": "2026-04-03", "title": "Rash", "severity": 4,
                                                      "photos": [{"id": two["id"], "alt": ""}]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.kim.get(url).status_code, 404)
        self.assertFalse(os.path.exists(path), "its file is removed")
        self.assertEqual(self.kim.get(f"/api/profiles/{self.pid}/journal/photos/{two['id']}").headers["content-type"], "image/webp")
        # deleted with its entry
        db = dbm.connect()
        file2 = db.execute("SELECT file FROM journal_photos WHERE id=?", (two["id"],)).fetchone()["file"]
        db.close()
        self.assertEqual(self.robin.delete(self.url(f"/{jid}"), headers=H).status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(config.DATA_DIR, "profiles", str(self.pid), "journal", file2)))
        db = dbm.connect()
        self.assertIsNone(db.execute("SELECT 1 FROM journal_photos WHERE id=?", (two["id"],)).fetchone())
        # a pending photo left a day is swept away the next time one is sent
        db.execute("UPDATE journal_photos SET created=created-? WHERE entry_id IS NULL", (journal_photos.PENDING_SECONDS + 5,))
        left = db.execute("SELECT COUNT(*) FROM journal_photos WHERE entry_id IS NULL").fetchone()[0]
        db.close()
        self.assertGreater(left, 0)
        self.photo(PNG)
        db = dbm.connect()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM journal_photos WHERE entry_id IS NULL").fetchone()[0], 1)
        db.close()

    def test_photos_in_export_and_share_links(self):
        sent = self.photo(jpeg_with_metadata())
        self.add(kind="symptom", t="2026-04-03", title="Rash", severity=4, where=["l-forearm"], photos=[{"id": sent["id"], "alt": "Fictional patch"}])
        self.add(kind="mood", t="2026-04-03", mood=6, energy=3)
        r = self.lee.get(f"/api/profiles/{self.pid}/export")
        self.assertEqual(r.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            data = json.loads(z.read("data.json"))
            name = f"journal/photos/{sent['id']}.jpg"
            self.assertEqual(z.read(name), journal_photos.strip(jpeg_with_metadata())[0])
            readme = z.read("README.txt").decode()
        photo = next(e for e in data["journal"] if e["kind"] == "symptom")["photos"][0]
        self.assertEqual((photo["file"], photo["alt"]), (name, "Fictional patch"))
        self.assertIn("journal/photos/    the photos attached to journal entries: 1", readme)
        # loading the export again brings the journal back with its photos (#589)
        tmp = os.path.join(TMP, "export.zip")
        with open(tmp, "wb") as f:
            f.write(r.content)
        db = dbm.connect()
        pid2 = db.execute("INSERT INTO profiles(name, created) VALUES('Loaded (fictional)', 1)").lastrowid
        export.import_archive(db, pid2, tmp)
        back = journal.listing(db, pid2)
        self.assertEqual(sorted(e["kind"] for e in back), ["mood", "symptom"])
        photo = next(e for e in back if e["kind"] == "symptom")["photos"][0]
        self.assertEqual(photo["alt"], "Fictional patch")
        self.assertEqual(journal_photos.get(db, pid2, photo["id"])[0], journal_photos.strip(jpeg_with_metadata())[0])
        db.close()
        # share links: offered once there are photos, never shown unless ticked, and only with the journal
        links = f"/api/profiles/{self.pid}/links"
        offered = [s["id"] for s in self.robin.get(links).json()["sections"]]
        self.assertEqual(offered[offered.index("journal"):offered.index("journal") + 2], ["journal", "journal_photos"])
        self.assertEqual(self.robin.post(links, json={"sections": ["journal_photos"], "hours": 2}, headers=H).status_code, 400)
        def view(sections, **extra):
            token = self.robin.post(links, json={"sections": sections, "hours": 2}, headers=H).json()["path"].split("#")[1]
            return token, client().post("/api/share/view", json={"token": token, **extra}, headers=H)
        token, r = view(["journal"])
        j = r.json()["journal"]
        rash = next(e for e in j if e["kind"] == "symptom")
        self.assertEqual(rash["where"], ["Left forearm"])
        self.assertNotIn("photos", rash)
        self.assertNotIn("Fictional patch", r.text)
        self.assertEqual(next(e for e in j if e["kind"] == "mood")["mood"], 6)
        r = client().post("/api/share/view", json={"token": token, "photo": sent["id"]}, headers=H)
        self.assertEqual((r.status_code, r.text), (404, "Not found.\n"), "not ticked: not served")
        token, r = view(["journal", "journal_photos"])
        self.assertEqual(next(e for e in r.json()["journal"] if e["kind"] == "symptom")["photos"], [{"id": sent["id"], "alt": "Fictional patch"}])
        r = client().post("/api/share/view", json={"token": token, "photo": sent["id"]}, headers=H)
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/jpeg"))
        self.assertNotIn(MAKE, r.content)
        self.assertEqual(client().post("/api/share/view", json={"token": token, "photo": sent["id"] + 1000}, headers=H).status_code, 404)
        out = self.robin.post(links, json={"sections": ["journal", "journal_photos"], "hours": 2, "from": "2026-05-01"}, headers=H).json()
        r = client().post("/api/share/view", json={"token": out["path"].split("#")[1], "photo": sent["id"]}, headers=H)
        self.assertEqual(r.status_code, 404, "outside the link's dates")


if __name__ == "__main__":
    unittest.main(verbosity=2)
