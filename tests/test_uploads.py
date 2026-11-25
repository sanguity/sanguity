#!/usr/bin/env python3
"""Upload tests (issue #19): the import page's uploads, through the API and server/uploads.py. Who may upload,
paths and zips that are refused, limits, encryption on disk, the decrypted copies importers read, export,
backups, deleting the profile and start-up tidying. Fictional data only, in a temporary folder.

  pip install -r requirements-dev.txt
  python3 tests/test_uploads.py
"""
import atexit, io, json, os, shutil, stat, sys, tempfile, time, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-uploads-")
atexit.register(shutil.rmtree, TMP, True)
os.environ.update(SANGUITY_DATA=os.path.join(TMP, "data"), SANGUITY_RECORDS=os.path.join(TMP, "records"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"), SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"),
                  SANGUITY_BACKUPS=os.path.join(TMP, "backups"))
sys.path.insert(0, ROOT)

import logging   # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, atrest, config, crypto, db as dbm, export, main, profiles as prof, uploads   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"edna": "edna keeps a long passphrase", "vera": "vera views with a passphrase", "stan": "stan is a stranger here"}
LAB_CSV = b"date,test,value,unit\n2026-01-05,Hemoglobin,13.9,g/dL\n2026-03-02,Hemoglobin,14.1,g/dL\n"
CCDA = b'<?xml version="1.0"?><ClinicalDocument xmlns="urn:hl7-org:v3"><title>Fictional summary</title></ClinicalDocument>'


def zip_bytes(entries, method=zipfile.ZIP_DEFLATED):
    """entries: {name: bytes}, or (ZipInfo, bytes) pairs for entries that need their own attributes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for e in entries.items() if isinstance(entries, dict) else entries:
            z.writestr(*e)
    return buf.getvalue()


class Uploads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(main.app).__enter__()   # start-up: database, key, pepper
        db = dbm.connect()
        cls.uid = {}
        for name in PW:
            cls.uid[name] = accounts.create(db, name, password=PW[name])[0]
        cls.pid = prof.create_profile(db, "Fictional Person", owner_id=cls.uid["edna"])
        db.execute("UPDATE memberships SET role='editor' WHERE profile_id=?", (cls.pid,))   # an editor, not the owner
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (cls.uid["vera"], cls.pid))
        cls.other = prof.create_profile(db, "Someone else", owner_id=cls.uid["stan"])
        db.close()
        cls.c = {n: cls.login(n) for n in PW}

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    @classmethod
    def login(cls, name):
        c = TestClient(main.app)
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def setUp(self):
        self.db = dbm.connect()
        self.saved = {k: getattr(config, k) for k in dir(config) if k.startswith("UPLOAD_")}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(config, k, v)
        self.db.close()

    # ---- helpers

    def new(self, who="edna", pid=None):
        r = self.c[who].post(f"/api/profiles/{pid or self.pid}/uploads", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def put(self, upload_id, path, body, who="edna", pid=None):
        return self.c[who].put(f"/api/profiles/{pid or self.pid}/uploads/{upload_id}/files", params={"path": path},
                               content=body, headers={**H, "Content-Type": "application/octet-stream"})

    def finish(self, upload_id, who="edna"):
        return self.c[who].post(f"/api/profiles/{self.pid}/uploads/{upload_id}/finish", headers=H)

    def upload(self, files, who="edna"):
        """Send {path: bytes} and finish; returns (upload id, finish response)."""
        u = self.new(who)
        for p, b in files.items():
            r = self.put(u, p, b, who)
            self.assertEqual(r.status_code, 200, f"{p}: {r.text}")
        return u, self.finish(u, who)

    def folder_files(self, upload_id):
        d = uploads.folder(self.pid, upload_id)
        return [os.path.join(r, n) for r, _, ns in os.walk(d) for n in ns]

    # ---- the browser's ways in: a zip, a folder, single files

    def test_editor_uploads_a_zip(self):
        z = zip_bytes({"export/summary.xml": CCDA, "export/labs/results.csv": LAB_CSV, "export/attachments.zip": zip_bytes({"a.txt": b"inner"}),
                       "__MACOSX/export/._summary.xml": b"junk", "export/empty/": b""})
        u, r = self.upload({"portal-export.zip": z})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["files"], 3)
        # the only file was a zip: what it held is at the top; a zip inside stays a zip; macOS junk is left out
        self.assertEqual(sorted(uploads.manifest(self.db, self.pid, u)),
                         ["export/attachments.zip", "export/labs/results.csv", "export/summary.xml"])
        info = self.c["edna"].get(f"/api/profiles/{self.pid}/uploads/{u}").json()
        self.assertEqual((info["state"], info["files"]), ("ready", 3))
        with uploads.open_plain(self.db, self.pid, u) as files:
            with open(files["export/labs/results.csv"], "rb") as f:
                self.assertEqual(f.read(), LAB_CSV)
            with zipfile.ZipFile(files["export/attachments.zip"]) as inner:
                self.assertEqual(inner.read("a.txt"), b"inner")
        # logged, with no file names
        act = self.db.execute("SELECT action, detail, actor_id FROM activity WHERE profile_id=? ORDER BY id DESC LIMIT 1", (self.pid,)).fetchone()
        self.assertEqual((act["action"], act["actor_id"]), ("upload", self.uid["edna"]))
        self.assertEqual(json.loads(crypto.dec(act["detail"])), {"upload": u, "files": 3, "bytes": r.json()["bytes"]})
        # finished: nothing more can be added, and it can't be finished twice
        self.assertEqual(self.put(u, "late.txt", b"x").status_code, 409)
        self.assertEqual(self.finish(u).status_code, 409)

    def test_editor_uploads_a_folder(self):
        files = {"MyChart/IHE_XDM/Person1/DOC0001.XML": CCDA, "MyChart/IHE_XDM/Person1/DOC0002.XML": CCDA,
                 "MyChart/labs.csv": LAB_CSV, "MyChart/more.zip": zip_bytes({"x/notes.txt": b"notes"})}
        u, r = self.upload(files)
        self.assertEqual(r.status_code, 200, r.text)
        # several files: a zip among them unpacks into a folder named after it
        self.assertEqual(sorted(uploads.manifest(self.db, self.pid, u)),
                         ["MyChart/IHE_XDM/Person1/DOC0001.XML", "MyChart/IHE_XDM/Person1/DOC0002.XML",
                          "MyChart/labs.csv", "MyChart/more/x/notes.txt"])
        self.assertEqual(r.json()["bytes"], 2 * len(CCDA) + len(LAB_CSV) + len(b"notes"))
        self.assertEqual(len(self.folder_files(u)), 4)   # the zip itself is gone, its file kept
        # paths are normalised
        u2 = self.new()
        self.assertEqual(self.put(u2, "a//./b.txt", b"x").json(), {"path": "a/b.txt", "bytes": 1})
        # the same path twice, in any case, or a file where a folder is needed, is refused
        self.assertEqual(self.put(u2, "a/b.txt", b"y").status_code, 409)
        self.assertEqual(self.put(u2, "c.txt", b"1").status_code, 200)
        self.assertEqual(self.put(u2, "C.TXT", b"2").status_code, 409)   # letter case doesn't make it another file
        self.assertEqual(self.finish(u2).status_code, 200)
        u3 = self.new()   # a file and a folder of the same name: only the whole shows it, so it's checked at the end
        self.put(u3, "a", b"file"), self.put(u3, "a/b", b"nested")
        self.assertEqual(self.finish(u3).status_code, 409)
        self.assertEqual(self.finish(self.new()).status_code, 400)   # nothing sent

    def test_who_may_upload(self):
        # a viewer can't start an upload, or add to someone else's
        self.assertEqual(self.c["vera"].post(f"/api/profiles/{self.pid}/uploads", headers=H).status_code, 403)
        u = self.new()
        self.assertEqual(self.put(u, "x.csv", LAB_CSV, who="vera").status_code, 403)
        self.assertEqual(self.c["vera"].post(f"/api/profiles/{self.pid}/uploads/{u}/finish", headers=H).status_code, 403)
        self.assertEqual(self.c["vera"].delete(f"/api/profiles/{self.pid}/uploads/{u}", headers=H).status_code, 403)
        self.assertEqual(self.c["vera"].get(f"/api/profiles/{self.pid}/uploads/{u}").status_code, 403)
        # a stranger doesn't learn the profile exists
        self.assertEqual(self.c["stan"].post(f"/api/profiles/{self.pid}/uploads", headers=H).status_code, 404)
        self.assertEqual(self.put(u, "x.csv", LAB_CSV, who="stan").status_code, 404)
        # an upload of one profile can't be reached through another
        s = self.new("stan", self.other)
        self.assertEqual(self.put(s, "x.csv", LAB_CSV, who="stan", pid=self.pid).status_code, 404)
        self.assertEqual(self.put(u, "x.csv", LAB_CSV, who="stan", pid=self.other).status_code, 404)
        # signed out, and without the cross-site header
        self.assertEqual(TestClient(main.app).post(f"/api/profiles/{self.pid}/uploads", headers=H).status_code, 401)
        r = self.c["edna"].put(f"/api/profiles/{self.pid}/uploads/{u}/files", params={"path": "x"}, content=b"x")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c["edna"].post(f"/api/profiles/{self.pid}/uploads").status_code, 403)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM upload_files WHERE upload_id=?", (u,)).fetchone()[0], 0)
        # the owner may too
        self.db.execute("UPDATE memberships SET role='owner' WHERE profile_id=? AND user_id=?", (self.pid, self.uid["edna"]))
        try:
            self.assertEqual(self.put(u, "x.csv", LAB_CSV).status_code, 200)
        finally:
            self.db.execute("UPDATE memberships SET role='editor' WHERE profile_id=? AND user_id=?", (self.pid, self.uid["edna"]))

    # ---- refused paths and zips

    def test_traversal_paths_are_refused(self):
        u = self.new()
        for bad in ("../escape.txt", "a/../../escape.txt", "..", "/etc/passwd", "C:/Windows/x.txt", "c:x.txt",
                    "a\\..\\b.txt", "a\x00b.txt", "a\nb.txt", "", "/", "./", "a/" + "b" * 300):
            r = self.put(u, bad, b"x")
            self.assertEqual(r.status_code, 400, f"{bad!r}: {r.text}")
        self.assertEqual(self.folder_files(u), [])   # nothing was written for any of them
        with self.assertRaises(uploads.UploadError):
            uploads.clean_path("a/../b")
        self.assertEqual(uploads.clean_path("./a/b/"), "a/b")

    def test_hostile_zips_are_refused(self):
        link = zipfile.ZipInfo("export/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = {
            "symlink": [(link, "/etc/passwd")],
            "dotdot": {"../../escape.txt": b"x"},
            "absolute": {"/abs/escape.txt": b"x"},
            "drive": {"C:/escape.txt": b"x"},
            "bomb": {"bomb.txt": b"\0" * (40 << 20)},   # 40 MB of zeros compress to about 40 KB
        }
        for what, entries in cases.items():
            u = self.new()
            self.assertEqual(self.put(u, "export.zip", zip_bytes(entries)).status_code, 200)
            r = self.finish(u)
            self.assertIn(r.status_code, (400, 413), f"{what}: {r.text}")
            if what == "bomb":
                self.assertIn("zip bomb", r.json()["detail"])
            # nothing unpacked is kept; the upload is still receiving, and only the zip is on disk
            self.assertEqual(len(self.folder_files(u)), 1, what)
            self.assertEqual(self.c["edna"].get(f"/api/profiles/{self.pid}/uploads/{u}").json()["state"], "receiving")
        # a lower ratio is a setting; something under 1 MB is never a bomb
        config.UPLOAD_MAX_ZIP_RATIO = 5000
        self.assertEqual(self.upload({"export.zip": zip_bytes({"zeros.bin": b"\0" * (8 << 20)})})[1].status_code, 200)
        config.UPLOAD_MAX_ZIP_RATIO = self.saved["UPLOAD_MAX_ZIP_RATIO"]
        self.assertEqual(self.upload({"small.zip": zip_bytes({"zeros.bin": b"\0" * (512 << 10)})})[1].status_code, 200)
        # not a zip after all: kept as a file
        u, r = self.upload({"not-really.zip": b"just text"})
        self.assertEqual((r.status_code, uploads.manifest(self.db, self.pid, u)), (200, ["not-really.zip"]))
        # a damaged zip is refused with a reason
        u, r = self.upload({"broken.zip": zip_bytes({"a.csv": LAB_CSV})[:-30]})
        self.assertEqual(r.status_code, 400, r.text)

    def test_limits(self):
        # files per upload: sent one by one, and inside a zip
        config.UPLOAD_MAX_FILES = 3
        u = self.new()
        for i in range(3):
            self.assertEqual(self.put(u, f"f{i}.csv", LAB_CSV).status_code, 200)
        self.assertEqual(self.put(u, "f3.csv", LAB_CSV).status_code, 413)
        u, r = self.upload({"many.zip": zip_bytes({f"f{i}.csv": LAB_CSV for i in range(4)})})
        self.assertEqual(r.status_code, 413, r.text)
        config.UPLOAD_MAX_FILES = self.saved["UPLOAD_MAX_FILES"]
        # bytes per file: refused as it streams (or by its length before), and nothing is left on disk
        config.UPLOAD_MAX_FILE = 1000
        u = self.new()
        r = self.put(u, "big.bin", b"x" * 1001)
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("at most 1000 bytes", r.json()["detail"])
        r = self.put(u, "big.bin", iter([b"x" * 600, b"x" * 600]))   # chunked: no length to go by
        self.assertEqual(r.status_code, 413, r.text)
        self.assertEqual(self.folder_files(u), [])
        self.assertEqual(self.put(u, "ok.bin", b"x" * 1000).status_code, 200)
        u, r = self.upload({"big.zip": zip_bytes({"big.txt": b"\0" * 3000})})   # unpacked files count too
        self.assertEqual(r.status_code, 413, r.text)
        config.UPLOAD_MAX_FILE = self.saved["UPLOAD_MAX_FILE"]
        # bytes per upload, altogether
        config.UPLOAD_MAX_TOTAL = 2500
        u = self.new()
        self.assertEqual(self.put(u, "a.bin", b"x" * 1500).status_code, 200)
        r = self.put(u, "b.bin", b"x" * 1500)
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("altogether", r.json()["detail"])
        self.assertEqual(self.c["edna"].get("/api/uploads/limits").json()["total_bytes"], 2500)

    # ---- at rest, and the plaintext importers read

    def test_files_are_encrypted_on_disk(self):
        secret = b"Fictional Person, MRN 000-TEST, Hemoglobin 13.9"
        u, r = self.upload({"records/very-private-name.txt": secret, "records/bundle.zip": zip_bytes({"inner-private.txt": secret * 3})})
        self.assertEqual(r.status_code, 200, r.text)
        files = self.folder_files(u)
        self.assertEqual(len(files), 2)
        for p in files:
            with open(p, "rb") as f:
                data = f.read()
            self.assertEqual(data[:4], crypto.STREAM, p)
            self.assertNotIn(b"Hemoglobin", data)
            self.assertNotIn("private", os.path.basename(p))   # names on disk are random
        row = self.db.execute("SELECT manifest FROM uploads WHERE id=?", (u,)).fetchone()
        self.assertTrue(row["manifest"].startswith(crypto.FIELD))
        self.assertNotIn("private", row["manifest"])
        # while receiving, each path is encrypted in its row
        u2 = self.new()
        self.put(u2, "pending-private.txt", secret)
        self.assertTrue(self.db.execute("SELECT path FROM upload_files WHERE upload_id=?", (u2,)).fetchone()[0].startswith(crypto.FIELD))
        self.assertEqual(uploads.manifest(self.db, self.pid, u2), ["pending-private.txt"])

    def test_open_plain(self):
        u, _ = self.upload({"a/labs.csv": LAB_CSV, "b/summary.xml": CCDA})
        with uploads.open_plain(self.db, self.pid, u) as files:
            self.assertEqual(sorted(files), ["a/labs.csv", "b/summary.xml"])
            tmp = os.path.dirname(os.path.dirname(files["a/labs.csv"]))
            self.assertTrue(os.path.realpath(tmp).startswith(os.path.realpath(tempfile.gettempdir())))
            if os.name != "nt":
                self.assertEqual(os.stat(tmp).st_mode & 0o777, 0o700)
                self.assertEqual(os.stat(files["a/labs.csv"]).st_mode & 0o777, 0o600)
            with open(files["b/summary.xml"], "rb") as f:
                self.assertEqual(f.read(), CCDA)
        self.assertFalse(os.path.exists(tmp))
        # removed on an error too
        with self.assertRaises(ZeroDivisionError):
            with uploads.open_plain(self.db, self.pid, u) as files:
                tmp = os.path.dirname(os.path.dirname(files["a/labs.csv"]))
                1 / 0
        self.assertFalse(os.path.exists(tmp))
        # only a finished upload
        u2 = self.new()
        with self.assertRaises(uploads.UploadError):
            with uploads.open_plain(self.db, self.pid, u2):
                pass
        # no room in the temporary folder for its decrypted copy (#592: the TrueNAS app's tmpfs): refused before
        # anything is decrypted, saying how much it needs; and so is one that fills it as it's decrypted
        from unittest import mock
        before = set(os.listdir(tempfile.gettempdir()))
        with mock.patch.object(uploads.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**9, 10**9 - 100, 100)):
            with self.assertRaises(uploads.UploadError) as e:
                with uploads.open_plain(self.db, self.pid, u):
                    self.fail("opened without room")
        self.assertEqual(e.exception.status, 507)
        self.assertIn("isn't room to read this upload", str(e.exception))
        self.assertIn("100 bytes free", str(e.exception))
        self.assertIn("Nothing was saved", str(e.exception))
        full = OSError(28, "No space left on device")
        full.errno = 28
        with mock.patch.object(uploads.crypto, "decrypt_to", side_effect=full):
            with self.assertRaises(uploads.UploadError) as e:
                with uploads.open_plain(self.db, self.pid, u):
                    pass
        self.assertEqual(e.exception.status, 507)
        self.assertFalse({n for n in set(os.listdir(tempfile.gettempdir())) - before if n.startswith("sanguity-upload-")})

    def test_interface_for_imports(self):   # what #20 calls, without the API
        u = uploads.create(self.db, self.pid, self.uid["edna"])
        self.assertEqual(uploads.add_file(self.db, self.pid, u, "fhir/Patient.json", io.BytesIO(b'{"resourceType":"Patient"}')),
                         {"path": "fhir/Patient.json", "bytes": 26})
        uploads.add_file(self.db, self.pid, u, "fhir/Observation.ndjson", iter([b'{"a":1}\n', b'{"a":2}\n']))
        self.assertEqual(uploads.finish(self.db, self.pid, u), {"files": 2, "bytes": 42})
        self.assertEqual(self.db.execute("SELECT state FROM uploads WHERE id=?", (u,)).fetchone()[0], "ready")
        self.assertEqual(uploads.manifest(self.db, self.pid, u), ["fhir/Patient.json", "fhir/Observation.ndjson"])
        with self.assertRaises(uploads.UploadError) as e:
            uploads.manifest(self.db, self.other, u)
        self.assertEqual(e.exception.status, 404)
        uploads.discard(self.db, self.pid, u)
        self.assertFalse(os.path.exists(uploads.folder(self.pid, u)))
        self.assertEqual(self.db.execute("SELECT state FROM uploads WHERE id=?", (u,)).fetchone()[0], "discarded")
        with self.assertRaises(uploads.UploadError):
            uploads.open_plain(self.db, self.pid, u).__enter__()
        # discarding through the API
        u2 = self.new()
        self.put(u2, "x.csv", LAB_CSV)
        self.assertEqual(self.c["edna"].delete(f"/api/profiles/{self.pid}/uploads/{u2}", headers=H).status_code, 200)
        self.assertFalse(os.path.exists(uploads.folder(self.pid, u2)))
        self.assertEqual(self.put(u2, "y.csv", LAB_CSV).status_code, 409)

    # ---- export, backups, deleting, tidying up

    def test_export_includes_uploads_and_loads_them_back(self):
        db = self.db
        pid = prof.create_profile(db, "Export check", owner_id=self.uid["edna"])
        src = os.path.join(TMP, "dataset")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "data.json"), "w", encoding="utf-8") as f:
            json.dump({"events": [{"id": 1, "t": "2026-01-05T09:00:00", "k": "lab", "title": "Fictional lab"}], "details": {}}, f)
        prof.import_dataset(db, pid, src)
        u = self.new(pid=pid)
        for p, b in {"portal/labs.csv": LAB_CSV, "portal/inner.zip": zip_bytes({"n.txt": b"n"})}.items():
            self.put(u, p, b, pid=pid)
        self.assertEqual(self.c["edna"].post(f"/api/profiles/{pid}/uploads/{u}/finish", headers=H).status_code, 200)
        unfinished = self.new(pid=pid)
        self.put(unfinished, "half.csv", LAB_CSV, pid=pid)
        r = self.c["edna"].get(f"/api/profiles/{pid}/export")
        self.assertEqual(r.status_code, 200)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertEqual(z.read(f"uploads/{u}/portal/labs.csv"), LAB_CSV)   # decrypted, as exports are
        self.assertIn(f"uploads/{u}/portal/inner/n.txt", z.namelist())
        self.assertFalse([n for n in z.namelist() if "half" in n])   # only finished uploads
        self.assertIn("uploads/", z.read("README.txt").decode())
        # loading the export elsewhere brings the uploads back, ready
        again = prof.create_profile(db, "Export loaded", owner_id=self.uid["edna"])
        export.import_archive(db, again, io.BytesIO(r.content))
        rows = db.execute("SELECT id, state FROM uploads WHERE profile_id=?", (again,)).fetchall()
        self.assertEqual([r_["state"] for r_ in rows], ["ready"])
        self.assertEqual(sorted(uploads.manifest(db, again, rows[0]["id"])), ["portal/inner/n.txt", "portal/labs.csv"])

    def test_backup_includes_uploads(self):
        from server import backup
        u, _ = self.upload({"backup/labs.csv": LAB_CSV})
        path = backup.create(dest=os.path.join(TMP, "backups"))
        m = backup.verify(path)
        names = [n for n in m["files"] if n.startswith(f"profiles/{self.pid}/uploads/{u}/")]
        self.assertEqual(len(names), 1)
        backup._check_key(path)   # opens with this key (the first sealed file may be a stream)

    def test_deleting_the_profile_removes_its_uploads(self):
        pid = prof.create_profile(self.db, "Short-lived", owner_id=self.uid["edna"])
        u = self.new(pid=pid)
        self.put(u, "x.csv", LAB_CSV, pid=pid)
        self.assertTrue(os.path.isdir(uploads.folder(pid, u)))
        r = self.c["edna"].request("DELETE", f"/api/profiles/{pid}", json={"confirm_name": "Short-lived"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(os.path.exists(uploads.folder(pid, u)))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM uploads WHERE profile_id=?", (pid,)).fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM upload_files WHERE upload_id=?", (u,)).fetchone()[0], 0)

    def test_stale_uploads_are_removed_at_start(self):
        stale, fresh = self.new(), self.new()
        self.put(stale, "old.csv", LAB_CSV), self.put(fresh, "new.csv", LAB_CSV)
        ready, _ = self.upload({"kept.csv": LAB_CSV})
        self.db.execute("UPDATE uploads SET created=created-2*86400 WHERE id IN (?, ?)", (stale, ready))
        orphan = os.path.join(prof.profile_dir(self.pid), "uploads", "999999")
        os.makedirs(orphan)
        open(os.path.join(orphan, "leftover"), "wb").close()
        self.assertEqual(uploads.cleanup(self.db), 1)
        self.assertFalse(os.path.exists(uploads.folder(self.pid, stale)))
        self.assertIsNone(self.db.execute("SELECT 1 FROM uploads WHERE id=?", (stale,)).fetchone())
        self.assertFalse(os.path.exists(orphan))
        self.assertTrue(os.path.isdir(uploads.folder(self.pid, fresh)))
        # a day-old finished upload no job ever read, nor any record holds, goes too (#933: see the test below)
        self.assertFalse(os.path.exists(uploads.folder(self.pid, ready)))

    def test_a_copy_a_stop_left_half_written_goes_at_start(self):   # #940
        """A file of a ready upload written again (uploads.rewrite, #555: an Apple Health export without its cycle
        records) is written beside it as <name>.tmp, sealed, then put in its place; a stop in between left the .tmp in
        the live upload's folder for good, in every backup. The start's tidying removes it, and any a file still being
        received left, and nothing else of a live upload."""
        u, _ = self.upload({"apple_health_export/export.xml": b"<HealthData/>"})
        receiving = self.new()
        self.put(receiving, "half.csv", LAB_CSV)
        before = {x: sorted(os.listdir(uploads.folder(self.pid, x))) for x in (u, receiving)}
        for x in (u, receiving):   # as crypto.StreamWriter leaves one, stopped before close() or abort()
            w = crypto.StreamWriter(os.path.join(uploads.folder(self.pid, x), before[x][0]))
            w.write(b"<HealthData>" + b"x" * 70000)
            w._f.close()
        self.assertTrue(all(any(n.endswith(".tmp") for n in os.listdir(uploads.folder(self.pid, x))) for x in (u, receiving)))
        uploads.cleanup(self.db)
        self.assertEqual({x: sorted(os.listdir(uploads.folder(self.pid, x))) for x in (u, receiving)}, before, "the .tmp gone, the rest kept")
        with uploads.open_plain(self.db, self.pid, u) as files:
            with open(files["apple_health_export/export.xml"], "rb") as f:
                self.assertEqual(f.read(), b"<HealthData/>")

    def test_an_unclaimed_typed_in_file_goes_a_day_on(self):   # #933
        """A file sent for a record typed in that was never saved (the page closed, or Leave chosen) is an upload no job
        reads: at the first start a day on it goes, as one still receiving does, and is no longer in Export everything
        or a backup. Kept: one a saved record holds, one put back from an export, and one sent within the day."""
        pdf = b"%PDF-1.4\n% a fictional scan\n%%EOF\n"
        left, _ = self.upload({"scan.pdf": pdf})   # the page sends the file at once (import-records.js send)
        saved, _ = self.upload({"letter.pdf": pdf})
        r = self.c["edna"].post(f"/api/profiles/{self.pid}/manual", headers=H, json={"save": True, "items": [
            {"kind": "document", "date": "2026-07-01", "title": "Referral letter (fictional)", "doctype": "letter",
             "upload": str(saved), "file": "letter.pdf"}]})
        self.assertEqual(r.status_code, 200, r.text)
        today, _ = self.upload({"today.pdf": pdf})
        put_back = uploads.create(self.db, self.pid, None, put_back=True)   # as export.import_archive puts an export's back
        uploads.add_file(self.db, self.pid, put_back, "portal/labs.csv", io.BytesIO(LAB_CSV))
        uploads.finish(self.db, self.pid, put_back, unzip=False)
        self.db.execute("UPDATE uploads SET created=created-2*86400 WHERE id IN (?, ?, ?)", (left, saved, put_back))

        def exported():
            z = zipfile.ZipFile(io.BytesIO(self.c["edna"].get(f"/api/profiles/{self.pid}/export").content))
            return {n.split("/")[1] for n in z.namelist() if n.startswith("uploads/")}
        self.assertIn(str(left), exported())
        uploads.cleanup(self.db)
        state = dict(self.db.execute("SELECT id, state FROM uploads WHERE id IN (?, ?, ?, ?)", (left, saved, today, put_back)).fetchall())
        self.assertEqual(state, {left: "discarded", saved: "ready", today: "ready", put_back: "ready"})
        self.assertFalse(os.path.exists(uploads.folder(self.pid, left)))
        for kept in (saved, today, put_back):
            self.assertTrue(os.path.isdir(uploads.folder(self.pid, kept)), kept)
        self.assertNotIn(str(left), exported())
        self.assertTrue({str(saved), str(today), str(put_back)} <= exported())
        from server import backup
        path = backup.create(dest=os.path.join(TMP, "backups-933"))
        self.assertFalse([n for n in backup.verify(path)["files"] if n.startswith(f"profiles/{self.pid}/uploads/{left}/")])

    def test_an_unclaimed_file_goes_though_its_account_was_removed(self):   # #944
        """#933's sweep keeps an export's uploads put back with server.manage import, which no account made, and took
        an upload made by none for one of them: a file attached to a record typed in that was never saved, its account
        removed within the day (its maker NULL since), was kept for good. Now an upload put back from an export says so
        (uploads.put_back), and every other one no job read and no record holds goes a day on, whoever made it."""
        pdf = b"%PDF-1.4\n% a fictional scan\n%%EOF\n"
        db = self.db
        temp = accounts.create(db, "temp-944", password="a temporary account's long passphrase")[0]
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'editor')", (temp, self.pid))
        c = self.login_as("temp-944", "a temporary account's long passphrase")
        r = c.post(f"/api/profiles/{self.pid}/uploads", headers=H)
        left = r.json()["id"]
        self.assertEqual(c.put(f"/api/profiles/{self.pid}/uploads/{left}/files", params={"path": "scan.pdf"}, content=pdf,
                               headers={**H, "Content-Type": "application/octet-stream"}).status_code, 200)
        self.assertEqual(c.post(f"/api/profiles/{self.pid}/uploads/{left}/finish", headers=H).status_code, 200)
        accounts.remove(db, temp)
        self.assertIsNone(db.execute("SELECT created_by FROM uploads WHERE id=?", (left,)).fetchone()[0], "made by no one now")
        moved = prof.create_profile(db, "Put back (fictional)", owner_id=self.uid["edna"])   # an export's upload, put back
        src = os.path.join(TMP, "export-944.zip")
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("data.json", json.dumps({"events": [{"id": 1, "t": "2026-01-05T09:00:00", "k": "visit", "title": "Visit (fictional)"}],
                                                "details": {}, "labs": [], "costs": []}))
            z.writestr("uploads/4/portal/labs.csv", LAB_CSV)
        export.import_archive(db, moved, src)
        (put_back,) = [r_[0] for r_ in db.execute("SELECT id FROM uploads WHERE profile_id=?", (moved,))]
        db.execute("UPDATE uploads SET created=created-2*86400 WHERE id IN (?, ?)", (left, put_back))
        uploads.cleanup(db)
        state = dict(db.execute("SELECT id, state FROM uploads WHERE id IN (?, ?)", (left, put_back)).fetchall())
        self.assertEqual(state, {left: "discarded", put_back: "ready"})
        self.assertFalse(os.path.exists(uploads.folder(self.pid, left)))
        self.assertTrue(os.path.isdir(uploads.folder(moved, put_back)))

    @staticmethod
    def login_as(name, pw):
        c = TestClient(main.app)
        r = c.post("/api/login", json={"username": name, "password": pw}, headers=H)
        assert r.status_code == 200, r.text
        return c

    def test_key_rotation_reseals_uploads(self):
        u, _ = self.upload({"rotate/labs.csv": LAB_CSV})
        old = crypto.current_key_id()
        crypto.add_key(config.KEY_FILE)
        crypto.keyring.reset()
        try:
            self.assertEqual(uploads.manifest(self.db, self.pid, u), ["rotate/labs.csv"])   # the old line still opens it
            self.assertGreater(atrest.seal_everything(self.db)["files"], 0)
            new = crypto.current_key_id()
            self.assertNotEqual(old, new)
            for p in self.folder_files(u):
                with open(p, "rb") as f:
                    self.assertEqual(f.read(8), crypto.STREAM + new)
            self.assertEqual(atrest.seal_everything(self.db), {"files": 0, "fields": 0})
            with uploads.open_plain(self.db, self.pid, u) as files:
                with open(files["rotate/labs.csv"], "rb") as f:
                    self.assertEqual(f.read(), LAB_CSV)
        finally:
            crypto.keyring.reset()


class Streams(unittest.TestCase):
    """crypto.py's stream format: written and read a chunk at a time; tampering is caught."""

    @classmethod
    def setUpClass(cls):
        crypto.make_key_file(config.KEY_FILE)   # may run before the app has started and made it
        crypto.keyring.reset()

    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=TMP)

    def write(self, data, chunk=16, pieces=7):
        path = os.path.join(self.dir, "s")
        with crypto.StreamWriter(path, chunk) as w:
            for i in range(0, len(data), pieces):
                w.write(data[i:i + pieces])
        return path

    def test_round_trip(self):
        for n in (0, 1, 15, 16, 17, 32, 33, 100):
            data = os.urandom(n)
            path = self.write(data)
            self.assertEqual(crypto.read_file(path), data, n)
            self.assertEqual(b"".join(crypto.iter_file(path)), data, n)
            self.assertTrue(crypto.file_is_encrypted(path))
            chunks = max(1, -(-n // 16))   # full chunks, then the rest; an empty file is one empty last chunk
            self.assertEqual(os.path.getsize(path), 19 + n + 16 * chunks, n)
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_tampering_is_caught(self):
        data = os.urandom(50)   # 4 chunks of 16: 16, 16, 16, 2
        path = self.write(data)
        with open(path, "rb") as f:
            blob = f.read()
        head, body = blob[:19], blob[19:]
        chunks = [body[i:i + 32] for i in range(0, len(body), 32)]
        bad = {
            "cut at a chunk": head + b"".join(chunks[:2]),
            "last chunk dropped": head + b"".join(chunks[:3]),
            "reordered": head + chunks[1] + chunks[0] + b"".join(chunks[2:]),
            "flipped bit": head + chunks[0][:5] + bytes([chunks[0][5] ^ 1]) + chunks[0][6:] + b"".join(chunks[1:]),
            "other header": head[:8] + bytes(7) + head[15:] + body,
        }
        for what, b in bad.items():
            with open(path, "wb") as f:
                f.write(b)
            with self.assertRaises(crypto.KeyError_, msg=what):
                crypto.read_file(path)

    def test_error_leaves_nothing(self):
        path = os.path.join(self.dir, "gone")
        with self.assertRaises(RuntimeError):
            with crypto.StreamWriter(path) as w:
                w.write(b"partial")
                raise RuntimeError
        self.assertEqual(os.listdir(self.dir), [])


class Schema(unittest.TestCase):
    def test_version_43_keeps_what_may_have_been_put_back(self):   # #944
        """At the upgrade, an upload no account made (its maker NULL) that no job read may be an export's put back, or
        one whose account was removed since: kept, as before. One a job read, or one an account made, isn't."""
        import sqlite3
        d = tempfile.mkdtemp(prefix="sanguity-schema43-")
        self.addCleanup(shutil.rmtree, d, True)
        db = sqlite3.connect(os.path.join(d, "schema-42.db"), isolation_level=None)
        for v, sql in enumerate(dbm.SCHEMA[:42], start=1):
            db.executescript("BEGIN;" + sql + f"PRAGMA user_version={v};COMMIT;")
        db.execute("INSERT INTO users(id, username, pw_hash, created) VALUES(3, 'ines', 'h', 1)")
        db.execute("INSERT INTO profiles(id, name, created) VALUES(2, 'Fictional', 1)")
        db.execute("""INSERT INTO uploads(id, profile_id, created, created_by, state) VALUES(10, 2, 1, NULL, 'ready'),
                      (11, 2, 1, NULL, 'ready'), (12, 2, 1, 3, 'ready')""")
        db.execute("INSERT INTO jobs(profile_id, upload_id, state, created) VALUES(2, 11, 'done', 1)")
        dbm.upgrade(db)
        self.assertEqual(dict(db.execute("SELECT id, put_back FROM uploads ORDER BY id").fetchall()), {10: 1, 11: 0, 12: 0})
        db.close()


if __name__ == "__main__":
    sys.exit(not unittest.main(exit=False, verbosity=2).result.wasSuccessful())
