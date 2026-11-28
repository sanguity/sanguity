#!/usr/bin/env python3
"""Searchable scanned documents (#1129; server/ocr.py, server/routes_ocr.py): a scanned picture or PDF read in the
background and found by the Timeline's search and the palette's, its text encrypted and never part of the records; a
PDF's own text used instead of recognition; a blank one marked so and not read again; the page and time caps said; a
stop in the middle; and the languages: importing a language file (its name, size and shape checked, then tried),
using it, removing it, and downloading one only while that's turned on, through Outside connections.

Tesseract is stood in for by tests/fake_tesseract.py. With SANGUITY_TEST_TESSERACT (a real tesseract) and
SANGUITY_TEST_TESSDATA (a folder with eng.traineddata), the real one reads the fixtures too (CI's Docker job runs
python -m server.ocr check in the image instead). Fictional data only.

  python3 tests/test_ocr.py
"""
import contextlib, http.server, io, json, os, shutil, struct, sys, tempfile, threading, time, unittest, zlib
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-ocr-test-")
FX = os.path.join(ROOT, "tests", "fixtures", "ocr")
FAKE_DATA = os.path.join(TMP, "tessdata")
CONTROL = os.path.join(TMP, "fake.json")
os.environ.pop("SANGUITY_SAVED_SETTINGS", None)
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_BACKUPS=os.path.join(TMP, "backups"), PASSWORD_MIN_LENGTH="15",
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), CONNECT_ALLOW_HTTP="1",
                  SANGUITY_TESSERACT=os.path.join(TMP, "bin", "tesseract"), SANGUITY_TESSDATA=FAKE_DATA)
for d in ("records", "keys", "bin", "tessdata", "backups"):
    os.makedirs(os.path.join(TMP, d))
sys.path.insert(0, ROOT)


def shaped(extra=b""):
    """Bytes shaped as a language file is: 24 parts, two of them present."""
    return struct.pack("<i", 24) + struct.pack("<24q", *([-1] * 22 + [196, 300])) + b"\0" * 200 + extra


with open(os.path.join(TMP, "bin", "tesseract"), "w") as f:   # the stand-in, as a program on its own
    f.write(f'#!/bin/sh\nexec "{sys.executable}" -I "{os.path.join(ROOT, "tests", "fake_tesseract.py")}" "{CONTROL}" "$@"\n')
os.chmod(os.path.join(TMP, "bin", "tesseract"), 0o755)
with open(os.path.join(FAKE_DATA, "eng.traineddata"), "wb") as f:
    f.write(shaped())

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, activity, backup, crypto, db as dbm, main, ocr, pdfdoc, profiles as prof, settings   # noqa: E402
import logging   # noqa: E402

logging.getLogger("sanguity.security").setLevel(logging.ERROR)
H = {"X-Sanguity": "1"}
PW = {"ada": "an admin of the fictional install", "kim": "a viewer of one fictional profile", "lee": "an owner of another"}
WORD = "zinnwaldite"


def control(**kw):
    with open(CONTROL, "w", encoding="utf-8") as f:
        json.dump(kw, f)


def fixture(name):
    with open(os.path.join(FX, name), "rb") as f:
        return f.read()


def pbm():
    """scan.pbm's width, height and rows (1 = black, each row whole bytes)."""
    data = fixture("scan.pbm")
    _, size, bits = data.split(b"\n", 2)
    w, h = map(int, size.split())
    return w, h, bits


def pdf(pages):
    """A PDF of pages, each ("image", {its stream's entries}, data) drawn over the whole page, or ("text", words)."""
    objs = [None, None]   # 1: the catalog, 2: the pages
    kids = []
    for kind, *rest in pages:
        if kind == "image":
            entries, data = rest
            w, h = entries["/Width"], entries["/Height"]
            pw, ph = w * 72 / 200, h * 72 / 200
            objs.append(("<< /Type /XObject /Subtype /Image " + " ".join(f"{k} {v}" for k, v in entries.items())
                         + f" /Length {len(data)} >>", data))
            im = len(objs)
            draw = f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im0 Do Q".encode()
            objs.append((f"<< /Length {len(draw)} >>", draw))
            objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] /Resources << /XObject << /Im0 {im} 0 R >> >> "
                        f"/Contents {len(objs)} 0 R >>")
        else:
            draw = f"BT /F1 12 Tf 72 700 Td ({rest[0]}) Tj ET".encode()
            objs.append((f"<< /Length {len(draw)} >>", draw))
            objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 << /Type /Font "
                        f"/Subtype /Type1 /BaseFont /Helvetica >> >> >> /Contents {len(objs)} 0 R >>")
        kids.append(len(objs))
    objs[0] = "<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, at = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        at.append(len(out))
        if isinstance(o, tuple):
            out += f"{i} 0 obj\n{o[0]}\nstream\n".encode() + o[1] + b"\nendstream\nendobj\n"
        else:
            out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{a:010d} 00000 n \n".encode() for a in at)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def flate_1bit():   # a PDF's 1-bit grey: 0 is black, as a fax's scan is kept
    w, h, bits = pbm()
    return ("image", {"/Width": w, "/Height": h, "/ColorSpace": "/DeviceGray", "/BitsPerComponent": 1, "/Filter": "/FlateDecode"},
            zlib.compress(bits.translate(bytes(255 - i for i in range(256)))))


def flate_grey():   # 8-bit grey, as a phone's scanning app keeps it
    w, h, bits = pbm()
    row = (w + 7) // 8
    px = bytes(0 if bits[y * row + x // 8] >> (7 - x % 8) & 1 else 255 for y in range(h) for x in range(w))
    return ("image", {"/Width": w, "/Height": h, "/ColorSpace": "/DeviceGray", "/BitsPerComponent": 8, "/Filter": "/FlateDecode"},
            zlib.compress(px))


def jpeg():
    w, h, _ = pbm()
    return ("image", {"/Width": w, "/Height": h, "/ColorSpace": "/DeviceGray", "/BitsPerComponent": 8, "/Filter": "/DCTDecode"},
            fixture("scan.jpg"))


def text_pdf():
    doc = pdfdoc.Document(title="A typed letter")
    page = doc.page()
    page.text(50, 740, f"A typed letter about {WORD}, with text of its own, so nothing is recognised", size=12)
    return doc.output()


DOCS = {   # file name: (its bytes, the record's title); sealed.pdf is kept sealed, as an imported document is
    "scan.png": (fixture("scan.png"), "Scanned letter"),
    "blank.png": (fixture("blank.png"), "Blank page"),
    "broken.jpg": (b"not really a picture " * 3, "Broken picture"),
    "fax.pdf": (pdf([flate_1bit()]), "Faxed report"),
    "grey.pdf": (pdf([flate_grey()]), "Phone scan"),
    "photo.pdf": (pdf([jpeg()]), "Photographed letter"),
    "typed.pdf": (text_pdf(), "Typed letter"),
    "sealed.pdf": (pdf([flate_1bit()]), "Sealed scan"),
    "notes.txt": (b"plain text, never read", "A text file"),
}


def dataset(folder, docs):
    os.makedirs(folder)
    events, details = [], {}
    for i, (name, (data, title)) in enumerate(docs.items(), 1):
        path = os.path.join(folder, name)
        if name.startswith("sealed"):
            crypto.write_file(path, data)
        else:
            with open(path, "wb") as f:
                f.write(data)
        events.append({"id": i, "t": f"2025-0{1 + i % 9}-1{i % 9}T10:00:00", "k": "document", "title": title})
        details[str(i)] = {"type": "file", "url": name}
    with open(os.path.join(folder, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"events": events, "details": details, "labs": [], "costs": []}, f)


class Ocr(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        control(log=os.path.join(TMP, "runs.log"))
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(ocr, "start"):   # the tests read, not the thread
            cls.ctx = TestClient(main.app).__enter__()
        db = dbm.connect()
        uid = {n: accounts.create(db, n, password=PW[n], is_admin=n == "ada")[0] for n in PW}
        cls.pid = prof.create_profile(db, "Robin (fictional)", owner_id=uid["ada"])
        cls.other = prof.create_profile(db, "Sam (fictional)", owner_id=uid["lee"])
        db.execute("INSERT INTO memberships(user_id, profile_id, role) VALUES(?, ?, 'viewer')", (uid["kim"], cls.pid))
        ocr._wake.clear()
        dataset(os.path.join(TMP, "set"), DOCS)
        prof.import_dataset(db, cls.pid, os.path.join(TMP, "set"))
        cls.woken = ocr._wake.is_set()
        cls.ids = {r["title"]: r["id"] for r in (json.loads(main.records.page(db, cls.pid)[0]))["events"]}
        cls.docs = {}
        for r in db.execute("SELECT id, path FROM documents WHERE profile_id=?", (cls.pid,)):
            cls.docs[os.path.basename(crypto.dec(r["path"]))] = r["id"]
        cls.status_before = ocr.status(db)["pass"]
        while ocr.step(db):
            pass
        db.close()
        cls.c = {}
        for n in PW:
            cls.c[n] = TestClient(main.app)
            assert cls.c[n].post("/api/login", json={"username": n, "password": PW[n]}, headers=H).status_code == 200
        db = dbm.connect()   # an admin with an authenticator app (admins need a second factor), once signed in
        db.execute("UPDATE users SET totp_secret=? WHERE username='ada'", (crypto.enc("JBSWY3DPEHPK3PXP"),))
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        shutil.rmtree(TMP, True)

    def row(self, name):
        db = dbm.connect()
        try:
            return db.execute("SELECT * FROM document_text WHERE doc_id=?", (self.docs[name],)).fetchone()
        finally:
            db.close()

    def search(self, q, who="ada", pid=None):
        return self.c[who].post(f"/api/profiles/{pid or self.pid}/documents/search", json={"q": q}, headers=H)

    # ------------------------------------------------------------ reading

    def test_a_import_wakes_the_reader_and_the_pass_counts_every_document(self):
        self.assertTrue(self.woken, "the import's documents wake the thread once it's saved")
        before = self.status_before
        self.assertEqual((before["total"], before["read"], before["waiting"]), (8, 0, 8), "the text file isn't one to read")
        r = self.c["ada"].get("/api/settings/install/ocr")
        self.assertEqual(r.status_code, 200, r.text)
        p = r.json()["pass"]
        self.assertEqual((p["total"], p["read"], p["waiting"], p["found"], p["none"], p["failed"]), (8, 8, 0, 6, 1, 1))
        self.assertTrue(r.json()["available"])
        self.assertEqual(self.c["kim"].get("/api/settings/install/ocr").status_code, 403, "an admin's alone")

    def test_b_a_scanned_picture_is_read_and_its_text_kept_encrypted(self):
        r = self.row("scan.png")
        self.assertEqual((r["state"], r["source"], r["langs"], r["tries"]), ("found", "ocr", "eng", 1))
        self.assertTrue(r["body"].startswith(crypto.FIELD), "encrypted at rest")
        self.assertIn(WORD, crypto.dec(r["body"]))
        db = dbm.connect()
        try:
            self.assertNotIn(WORD.encode(), main.records.page(db, self.pid)[0], "never part of the records")
        finally:
            db.close()

    def test_c_scanned_pdfs_are_read_from_their_pictures(self):
        for name in ("fax.pdf", "grey.pdf", "photo.pdf", "sealed.pdf"):
            r = self.row(name)
            self.assertEqual((r["state"], r["source"], r["pages"], r["read"], r["capped"]), ("found", "ocr", 1, 1, None), name)
            self.assertIn(WORD, crypto.dec(r["body"]), name)
        with open(os.path.join(TMP, "runs.log"), encoding="utf-8") as f:
            runs = [json.loads(x) for x in f]
        self.assertTrue(any("--dpi" in a and a[a.index("--dpi") + 1] == "200" for a in runs), "its resolution from the page's size")

    def test_d_a_pdf_with_its_own_text_uses_it_not_recognition(self):
        r = self.row("typed.pdf")
        self.assertEqual((r["state"], r["source"], r["read"]), ("found", "layer", 1))
        self.assertIn("with text of its own", crypto.dec(r["body"]))

    def test_e_blank_and_broken_are_marked_and_not_read_again(self):
        self.assertEqual(self.row("blank.png")["state"], "none")
        self.assertEqual((self.row("broken.jpg")["state"], self.row("broken.jpg")["why"]), ("failed", "unreadable"))
        self.assertIsNone(self.row("notes.txt"), "a text file isn't read")
        db = dbm.connect()
        try:
            self.assertFalse(ocr.step(db), "nothing is left to read: neither is tried again")
        finally:
            db.close()

    def test_f_search_finds_the_scanned_documents_records(self):
        r = self.search("Letter about  ZINNWALD")
        self.assertEqual(r.status_code, 200, r.text)
        found = set(r.json()["events"])
        want = {self.ids[t] for t in ("Scanned letter", "Faxed report", "Phone scan", "Photographed letter", "Sealed scan", "Typed letter")}
        self.assertEqual(found, want, "spaces and capitals as the Timeline's search takes them")
        self.assertEqual(self.search("text of its own").json()["events"], [self.ids["Typed letter"]])
        self.assertEqual(self.search("zi").json()["events"], [], "three letters or more")
        self.assertEqual(self.search("no such words anywhere").json()["events"], [])
        self.assertEqual(self.search(WORD, "kim").status_code, 200, "a viewer searches")
        self.assertEqual(self.search(WORD, "lee").status_code, 404, "not someone else's profile")
        self.assertEqual(self.search(WORD, "lee", self.other).json()["events"], [], "nor finds this one's from theirs")

    def test_g_a_documents_text_for_its_drawer(self):
        url = f"/api/profiles/{self.pid}/document-text/"
        r = self.c["kim"].get(url + self.docs["scan.png"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["state"], r.json()["source"]), ("found", "ocr"))
        self.assertIn(WORD, r.json()["text"])
        self.assertEqual(self.c["kim"].get(url + self.docs["blank.png"]).json()["state"], "none")
        self.assertEqual(self.c["kim"].get(url + self.docs["notes.txt"]).json()["state"], "not_read")
        self.assertEqual(self.c["lee"].get(url + self.docs["scan.png"]).status_code, 404, "not someone else's profile")
        self.assertEqual(self.c["lee"].get(f"/api/profiles/{self.other}/document-text/" + self.docs["scan.png"]).status_code, 404)

    def test_h_large_pdfs_are_capped_by_pages_and_time(self):
        path = os.path.join(TMP, "long.pdf")
        with open(path, "wb") as f:
            f.write(pdf([flate_1bit()] * (ocr.MAX_PAGES + 5)))
        got = ocr.read(path, "pdf")
        self.assertEqual((got["state"], got["pages"], got["read"], got["capped"]), ("found", ocr.MAX_PAGES + 5, ocr.MAX_PAGES, "pages"))
        with open(path, "wb") as f:
            f.write(pdf([flate_1bit()] * 4))
        control(sleep=1.2)
        try:
            with mock.patch.object(ocr, "MAX_SECONDS", 3):
                got = ocr.read(path, "pdf")
        finally:
            control(log=os.path.join(TMP, "runs.log"))
        self.assertEqual((got["state"], got["pages"], got["capped"]), ("found", 4, "time"))
        self.assertLess(got["read"], 4)

    def test_i_a_stop_in_the_middle_reads_it_again_twice_at_most(self):
        db = dbm.connect()
        try:
            doc = self.docs["scan.png"]
            db.execute("UPDATE document_text SET state='reading', tries=1, body=NULL WHERE doc_id=?", (doc,))
            ocr.recover(db)
            self.assertTrue(ocr.step(db), "read again after one stop")
            self.assertEqual((self.row("scan.png")["state"], self.row("scan.png")["tries"]), ("found", 2))
            db.execute("UPDATE document_text SET state='reading', tries=2 WHERE doc_id=?", (doc,))
            ocr.recover(db)
            self.assertEqual((self.row("scan.png")["state"], self.row("scan.png")["why"]), ("failed", "stopped"))
            self.assertFalse(ocr.step(db))
            db.execute("DELETE FROM document_text WHERE doc_id=?", (doc,))
            self.assertTrue(ocr.step(db))
        finally:
            db.close()

    def test_j_the_thread_reads_what_it_is_woken_for(self):
        db = dbm.connect()
        try:
            db.execute("DELETE FROM document_text WHERE doc_id=?", (self.docs["grey.pdf"],))
            ocr.start()
            try:
                ocr.wake()
                for _ in range(100):
                    if (self.row("grey.pdf") or {"state": None})["state"] == "found":
                        break
                    time.sleep(.1)
            finally:
                ocr.stop()
            self.assertEqual(self.row("grey.pdf")["state"], "found")
        finally:
            db.close()

    def test_k_off_without_tesseract(self):
        with mock.patch.object(ocr, "TESSERACT", "off"):
            self.assertFalse(ocr.available())
            self.assertFalse(self.c["ada"].get("/api/settings/install/ocr").json()["available"])

    # ------------------------------------------------------------ languages

    def put_language(self, name, data, who="ada"):
        return self.c[who].put(f"/api/settings/install/ocr/languages/{name}", content=data,
                               headers={**H, "Content-Type": "application/octet-stream"})

    def test_m_importing_a_language_file_is_checked(self):
        for name in ("Deu.traineddata", "de.traineddata", "deu.txt", "deu-x.traineddata", "a_very_long_language_name.traineddata"):
            r = self.put_language(name, shaped())
            self.assertEqual(r.status_code, 400, name)
            self.assertIn("named for its language's code", r.json()["detail"])
        self.assertIn("built in", self.put_language("eng.traineddata", shaped()).json()["detail"])
        self.assertIn("orientation", self.put_language("osd.traineddata", shaped()).json()["detail"])
        self.assertIn("isn't a Tesseract language file", self.put_language("deu.traineddata", b"\x7fELF not a language" * 9).json()["detail"])
        self.assertIn("couldn't use it", self.put_language("deu.traineddata", shaped(b"BROKEN")).json()["detail"])
        with mock.patch.object(ocr, "MAX_LANG_BYTES", 1000):
            self.assertIn("at most", self.put_language("deu.traineddata", shaped(b"x" * 2000)).json()["detail"])
        self.assertEqual(self.put_language("deu.traineddata", shaped(), "kim").status_code, 403, "an admin's alone")
        self.assertFalse(os.path.exists(os.path.join(ocr.LANG_DIR, "deu.traineddata")), "nothing kept of any")
        self.assertEqual([f for f in os.listdir(ocr.LANG_DIR) if f.endswith(".tmp")], [], "nor left behind")
        db = dbm.connect()
        try:
            db.execute("UPDATE sessions SET verified_at=0")
            self.assertEqual(self.put_language("deu.traineddata", shaped()).json()["detail"], "reauth_required")
            db.execute("UPDATE sessions SET verified_at=?", (dbm.now(),))
        finally:
            db.close()

    def test_n_a_language_imported_is_kept_used_backed_up_and_removed(self):
        r = self.put_language("deu.traineddata", shaped())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["added"]["name"], "German")
        path = os.path.join(ocr.LANG_DIR, "deu.traineddata")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertIn("languages/deu.traineddata", backup._data_files(), "backed up")
        self.assertEqual(r.json()["in_use"], ["eng"], "not used until it's chosen")
        r = self.c["ada"].put("/api/settings/install/ocr.languages", json={"value": ["deu"]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c["ada"].get("/api/settings/install/ocr").json()["in_use"], ["eng", "deu"])
        db = dbm.connect()
        try:
            db.execute("DELETE FROM document_text WHERE doc_id=?", (self.docs["photo.pdf"],))
            self.assertTrue(ocr.step(db))
            self.assertEqual(self.row("photo.pdf")["langs"], "eng+deu")
            self.assertEqual([a["detail"]["how"] for a in activity.for_accounts(db) if a["action"] == "ocr.language"], ["imported"])
        finally:
            db.close()
        self.assertEqual(self.c["ada"].delete("/api/settings/install/ocr/languages/eng", headers=H).status_code, 404)
        r = self.c["ada"].delete("/api/settings/install/ocr/languages/deu", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(r.json()["in_use"], ["eng"])
        self.assertEqual(settings.install_value("ocr.languages"), [], "taken out of the languages in use")
        self.assertEqual(self.c["ada"].put("/api/settings/install/ocr.languages", json={"value": ["eng"]}, headers=H).status_code, 400)

    def test_o_downloading_a_language_only_when_turned_on(self):
        served = []

        class Lang(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                served.append(self.path)
                body = shaped() if self.path.endswith("/fra.traineddata") else b"not found"
                self.send_response(200 if self.path.endswith("/fra.traineddata") else 404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Lang)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}/tesseract-ocr/tessdata_fast/fixed/"
        down = lambda code: self.c["ada"].post("/api/settings/install/ocr/download", json={"code": code}, headers=H)   # noqa: E731
        try:
            with mock.patch.object(ocr, "DOWNLOAD", base):
                r = down("fra")
                self.assertEqual(r.status_code, 409, r.text)
                self.assertNotIn("languages", self.c["ada"].get("/api/outside").json(), "not listed while it's off")
                self.assertEqual(served, [], "nothing contacted while it's off")
                self.assertEqual(self.c["ada"].put("/api/settings/install/ocr.downloads", json={"value": True}, headers=H).status_code, 200)
                view = self.c["ada"].get("/api/outside").json()
                self.assertEqual(view["languages"]["hosts"], [f"127.0.0.1:{srv.server_address[1]}"])
                self.assertIn(view["languages"]["hosts"][0], view["may_contact"])
                self.assertEqual(down("xyz").status_code, 400, "only the languages offered")
                r = down("fra")
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(served, ["/tesseract-ocr/tessdata_fast/fixed/fra.traineddata"])
                self.assertTrue(os.path.isfile(os.path.join(ocr.LANG_DIR, "fra.traineddata")))
                self.assertEqual(self.c["ada"].get("/api/outside").json()["languages"]["contacts"][0]["purpose"], ocr.PURPOSE)
                self.assertIn("answered 404", down("spa").json()["detail"])
                self.assertFalse(os.path.exists(os.path.join(ocr.LANG_DIR, "spa.traineddata")))
                self.assertNotIn("fra", [x["code"] for x in self.c["ada"].get("/api/settings/install/ocr").json()["offered"]])
        finally:
            srv.shutdown()
            self.c["ada"].put("/api/settings/install/ocr.downloads", json={"value": False}, headers=H)
            self.c["ada"].delete("/api/settings/install/ocr/languages/fra", headers=H)


@unittest.skipUnless(os.environ.get("SANGUITY_TEST_TESSERACT"), "SANGUITY_TEST_TESSERACT names no real tesseract")
class RealTesseract(unittest.TestCase):
    """The fixtures read by a real Tesseract, with a real English (tessdata_fast's, as the image has)."""

    def setUp(self):
        p = [mock.patch.object(ocr, "TESSERACT", os.environ["SANGUITY_TEST_TESSERACT"]),
             mock.patch.object(ocr, "TESSDATA", os.environ.get("SANGUITY_TEST_TESSDATA", "/usr/share/tessdata"))]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def test_the_fixtures(self):
        self.assertTrue(ocr.check()[0], ocr.check()[1])
        for name, data in (("scan.png", fixture("scan.png")), ("scan.jpg", fixture("scan.jpg")), ("fax.pdf", pdf([flate_1bit()])),
                           ("grey.pdf", pdf([flate_grey()])), ("photo.pdf", pdf([jpeg()]))):
            path = os.path.join(TMP, "real-" + name)
            with open(path, "wb") as f:
                f.write(data)
            got = ocr.read(path, name.rsplit(".", 1)[1])
            self.assertEqual(got["state"], "found", name)
            self.assertIn(WORD, got["text"].lower(), name)
        path = os.path.join(TMP, "real-blank.png")
        with open(path, "wb") as f:
            f.write(fixture("blank.png"))
        self.assertEqual(ocr.read(path, "png")["state"], "none")


if __name__ == "__main__":
    unittest.main(verbosity=1)
