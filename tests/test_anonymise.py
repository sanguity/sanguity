#!/usr/bin/env python3
"""tools/anonymise.py (#34): an anonymised copy of an export still imports, the same, and keeps none of the names,
identifiers or dates of the original.

Runs the tool over every fictional importer fixture (tests/fixtures/importers/<NAME>/<case>/ and the Apple Health
export), imports the original and the copy with server/importers, and compares what came out. Then the unsafe
inputs it must refuse, and the pieces (dates, free text, identifiers) on their own.

  python tests/test_anonymise.py
"""
import ast, io, json, os, re, shutil, stat, subprocess, sys, tempfile, unittest, zipfile
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import anonymise as A                  # noqa: E402
from server import importers as reg    # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "importers")
TOOL = os.path.join(ROOT, "tools", "anonymise.py")
CASES = [(name, os.path.join(FIX, name, case)) for name in sorted(os.listdir(FIX))
         for case in sorted(os.listdir(os.path.join(FIX, name))) if os.path.isdir(os.path.join(FIX, name, case))
         and name != "pdf-labs"]
CASES.append(("apple-health", os.path.join(ROOT, "tests", "fixtures", "apple_health", "apple_health_export")))
SEED = "test_anonymise"

# What the fixtures name: none of it may be left in a copy (words are matched whole, ignoring case).
PEOPLE = ["Robin", "Casey", "Fairweather", "Morgan", "Quill", "Rowan", "Ellis", "Jordan", "Pike", "Avery", "Lin",
          "Vale", "Okafor", "Rivera", "Jamie", "Patel", "Robin Example", "Jordan Example", "Example Person"]
ORGS = ["Maple Grove", "Riverbend", "Willowbrook", "Lakeshore", "Harbor Point", "Harbor View", "Lakeside",
        "Valley Reference", "Northfield", "Corner Pharmacy", "Riverside General", "Harbor Physical"]
IDS = ["TEST-CCD-0001", "TEST-VISIT-0002", "ENC-1001", "LAB-2001", "MED-3001", "PRB-4001", "4400123", "4400188",
       "BC2026041100071", "BC2026051900188", "UH26030100117", "UH26100700504", "PX-26-0001", "PX-26-0004",
       "EXAMPLE-0042", "FICTIONAL-MBI-0001", "FICT-1001", "7741203", "AUTH-55120", "bb-fict-1", "cs-fixture",
       "1.2.840.114350.1.13.999.2.7.3.698084.8", "doc-0003", "5f1c0a9e2b7d4c61"]
ISO = re.compile(r"(?<!\d)((?:19|20)\d\d)-(\d\d)-(\d\d)(?!\d)")
HL7 = re.compile(r"(?<![\d.])((?:19|20)\d\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?=\d{0,6}(?:[+-]\d{4})?\")")
US = re.compile(r"(?<![\d/])(\d\d)/(\d\d)/((?:19|20)\d\d)(?!\d)")


def counts(out):
    """What an import holds, for comparing an original with its copy."""
    d = out["data"]
    c = {k: len(d.get(k) or []) for k in ("events", "details", "labs", "costs", "problems", "upcoming", "milestones")}
    c["points"] = sum(len(s.get("pts") or []) for s in d.get("labs") or [])
    c["kinds"] = sorted(e["k"] for e in d.get("events") or [])
    c["labs"] = sorted(s["name"] for s in d.get("labs") or [] if not re.search(r"\(.*\)$", s["name"]))
    c["units"] = sorted(s.get("unit") or "" for s in d.get("labs") or [])
    if d.get("watch"):
        c["metrics"] = sorted((m["key"], len(m.get("pts") or [])) for m in d["watch"].get("metrics") or [])
        c["exercise"] = len(d["watch"].get("exercise") or [])
    return c


def texts(root):
    """(relative path, text) for every file under root, zips unpacked, decoded as UTF-8 or UTF-16."""
    def decode(raw):
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16")
        return raw.decode("utf-8", "replace")
    for d, _, names in os.walk(root):
        for n in names:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, root)
            yield rel, rel   # the path itself
            with open(p, "rb") as f:
                raw = f.read()
            if zipfile.is_zipfile(p):
                with zipfile.ZipFile(p) as z:
                    for i in z.infolist():
                        yield f"{rel}!{i.filename}", i.filename
                        yield f"{rel}!{i.filename}", decode(z.read(i))
            else:
                yield rel, decode(raw)


def original_dates(root):
    """{(y, m, d)} for the ISO, HL7 and US dates in the fixture files."""
    out = set()
    for rel, text in texts(root):
        for rx, order in ((ISO, (0, 1, 2)), (HL7, (0, 1, 2)), (US, (2, 0, 1))):
            for m in rx.finditer(text):
                if text[max(0, m.start() - 11):m.start()] == 'extension="':
                    continue   # a C-CDA template's version, not a date about anyone
                g = [int(x) for x in m.groups()]
                try:
                    date(g[order[0]], g[order[1]], g[order[2]])
                except ValueError:
                    continue   # 04/31/2025 in a fixture about bad dates
                out.add((g[order[0]], g[order[1]], g[order[2]], m.group(0)))
    return out


def run(src, dst, **kw):
    kw.setdefault("seed", SEED)
    return A.anonymise(src, dst, **kw)


class Fixtures(unittest.TestCase):
    """The tool over every fictional fixture."""

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="anonymise-test-")
        cls.out = {}
        for name, src in CASES:
            dst = os.path.join(cls.work, f"{name}-{os.path.basename(src)}")
            cls.out[(name, src)] = (dst, run(src, dst))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_imports_the_same(self):
        for (name, src), (dst, rep) in self.out.items():
            with self.subTest(fixture=os.path.relpath(src, ROOT)):
                files = reg.files_in(dst)
                self.assertEqual(reg.match(files)["importer"].NAME, name, "the registry still picks the same importer")
                a, b = reg.run(reg.files_in(src), name=name), reg.run(files, name=name)
                self.assertEqual(reg.check({"data": b["data"], "warnings": b["warnings"]}, files), [])
                self.assertEqual(counts(a), counts(b))
                self.assertEqual(len(a["warnings"]), len(b["warnings"]))
                self.assertEqual(rep["left_out"], [])

    def test_no_names_or_identifiers_left(self):
        for (name, src), (dst, rep) in self.out.items():
            with self.subTest(fixture=os.path.relpath(src, ROOT)):
                for rel, text in texts(dst):
                    for s in PEOPLE + ORGS:
                        self.assertIsNone(re.search(rf"(?i)(?<![A-Za-z]){re.escape(s)}(?![A-Za-z])", text),
                                          f"{s!r} is still in {rel}")
                    for s in IDS:
                        self.assertNotIn(s, text, f"{s!r} is still in {rel}")

    def test_dates_moved_by_whole_weeks(self):
        for (name, src), (dst, rep) in self.out.items():
            with self.subTest(fixture=os.path.relpath(src, ROOT)):
                off = timedelta(days=rep["offset_days"])
                self.assertEqual(off.days % 7, 0)
                self.assertLessEqual(off.days, -26 * 7)
                orig = original_dates(src)
                moved = {(d + off) for d in (date(y, m, dd) for y, m, dd, _ in orig)}
                out = "\n".join(t for _, t in texts(dst))
                for y, m, d, spelled in orig:
                    if date(y, m, d) in moved or (m, d) == (1, 1):
                        continue   # the same spelling is also a moved date, or a generalised birth date
                    self.assertNotIn(spelled, out, f"the original date {spelled} is still in the copy")
                if orig:
                    self.assertGreater(rep["changed"].get("dates", 0), 0)

    def test_order_and_gaps_kept(self):
        """Events keep their order and the days between them; times of day stay."""
        for (name, src), (dst, rep) in self.out.items():
            if name == "apple-health":
                continue
            with self.subTest(fixture=os.path.relpath(src, ROOT)):
                a = [e["t"] for e in reg.run(reg.files_in(src), name=name)["data"]["events"]]
                b = [e["t"] for e in reg.run(reg.files_in(dst), name=name)["data"]["events"]]
                off = timedelta(days=rep["offset_days"])
                self.assertEqual(len(a), len(b))
                for x, y in zip(sorted(a), sorted(b)):
                    self.assertEqual((date.fromisoformat(x[:10]) + off).isoformat() + x[10:], y)

    def test_birth_dates_generalised(self):
        for case, rel in (("bundle", "patient-everything.json"), ("blue-button", "Patient.ndjson")):
            dst, rep = self.out[("fhir", os.path.join(FIX, "fhir", case))]
            with open(os.path.join(dst, rel), encoding="utf-8") as f:
                text = f.read()
            births = re.findall(r'"birthDate": ?"([^"]+)"', text)
            self.assertTrue(births)
            for b in births:
                self.assertRegex(b, r"^\d{4}-01-01$")
            self.assertEqual(rep["changed"].get("birth dates"), 1)

    def test_notes_and_documents(self):
        dst, rep = self.out[("epic-mychart", os.path.join(FIX, "epic-mychart", "willowbrook-export"))]
        msgs = [os.path.join(d, n) for d, _, ns in os.walk(dst) for n in ns if n.endswith("_messages.json")]
        with open(msgs[0], encoding="utf-8") as f:
            data = json.load(f)
        bodies = [m["body"] for c in data["details"].values() for m in c["messages"]]
        self.assertTrue(all("Text removed." in b for b in bodies), bodies)
        self.assertGreater(rep["changed"]["notes replaced"], 5)
        pdfs = [os.path.join(d, n) for d, _, ns in os.walk(dst) for n in ns if n.endswith(".pdf")]
        self.assertTrue(pdfs)
        for p in pdfs:
            with open(p, "rb") as f:
                self.assertIn(b"Document removed by anonymise.py", f.read())
        self.assertEqual(rep["changed"]["documents replaced"], 1)
        _, bundle = self.out[("fhir", os.path.join(FIX, "fhir", "bundle"))]
        self.assertEqual(bundle["changed"]["documents replaced"], 2)

    def test_report_says_what_to_check(self):
        dst, rep = self.out[("carespace", os.path.join(FIX, "carespace", "full"))]
        text = rep["_report"].text(dst)
        self.assertIn("Changed:", text)
        self.assertIn("people's names", text)
        self.assertIn("free text were kept", text)   # the narrative tables are free text it kept
        self.assertIn("Open the copy and look through it before you share it", text)
        self.assertNotIn(str(abs(rep["offset_days"])), text, "the report doesn't give the offset away")


class Options(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="anonymise-test-")
        self.addCleanup(shutil.rmtree, self.work, True)

    def files(self, root):
        return {rel: text for rel, text in texts(root)}

    def test_same_seed_same_copy(self):
        src = os.path.join(FIX, "ccda", "maple-grove-ccd")
        a, b, c = (os.path.join(self.work, x) for x in "abc")
        run(src, a)
        run(src, b)
        run(src, c, seed="another")
        self.assertEqual(self.files(a), self.files(b))
        self.assertNotEqual(self.files(a), self.files(c))
        d, e = os.path.join(self.work, "d"), os.path.join(self.work, "e")
        A.anonymise(src, d)
        A.anonymise(src, e)   # no seed: a new key every run
        self.assertNotEqual(self.files(d), self.files(e))

    def test_keep_notes(self):
        src = os.path.join(FIX, "epic-mychart", "willowbrook-export")
        dst = os.path.join(self.work, "out")
        rep = run(src, dst, notes="scrub")
        text = "\n".join(t for _, t in texts(dst))
        self.assertIn("Should I worry about the WBC?", text)
        self.assertIn("Lungs are clear.", text)
        self.assertNotIn("Text removed.", text)
        self.assertNotIn("AUTH-55120", text)            # an identifier inside a letter
        self.assertNotIn("February 20, 2026", text)     # a date inside a letter
        self.assertNotIn("March 9", text)               # ... and one without a year
        self.assertIsNone(re.search(r"Rowan|Ellis|Pike", text))
        self.assertNotIn("notes replaced", rep["changed"])

    def test_keep_organisations(self):
        src = os.path.join(FIX, "claims-csv", "eob")
        dst = os.path.join(self.work, "out")
        run(src, dst, keep_orgs=True)
        with open(os.path.join(dst, "claims.csv"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Valley Reference Laboratory", text)
        self.assertIn("Lakeside Imaging Center", text)
        self.assertNotIn("Morgan Ellis", text)
        self.assertNotIn("PX-26-0001", text)

    def test_jitter(self):
        src = os.path.join(FIX, "fhir", "ndjson")
        dst = os.path.join(self.work, "out")
        rep = run(src, dst, jitter=10)
        a, b = reg.run(reg.files_in(src), name="fhir")["data"], reg.run(reg.files_in(dst), name="fhir")["data"]
        self.assertEqual(counts({"data": a}), counts({"data": b}))
        self.assertGreater(rep["changed"]["values jittered"], 0)
        moved = 0
        for sa, sb in zip(sorted(a["labs"], key=lambda s: s["name"]), sorted(b["labs"], key=lambda s: s["name"])):
            for pa, pb in zip(sa["pts"], sb["pts"]):
                decimals = len(repr(pa[1]).split(".")[1]) if "." in repr(pa[1]) else 0
                self.assertLessEqual(abs(pb[1] - pa[1]), abs(pa[1]) * 0.1 + 0.5 * 10 ** -decimals + 1e-9)
                self.assertEqual(pa[2:4], pb[2:4], "reference ranges are kept")
                moved += pb[1] != pa[1]
        self.assertGreater(moved, 0)

    def test_zip_in_zip_out(self):
        """A zipped export (with the zip of visit records inside it) gives a zip that imports the same."""
        src_dir = os.path.join(FIX, "epic-mychart", "willowbrook-export")
        zpath = os.path.join(self.work, "export.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            for d, _, ns in os.walk(src_dir):
                for n in ns:
                    p = os.path.join(d, n)
                    z.write(p, "willowbrook-export/" + os.path.relpath(p, src_dir))
        rep = run(zpath, None)
        out = rep["output"]
        self.assertTrue(out.endswith("-anonymised.zip") and zipfile.is_zipfile(out))
        self.assertNotIn("willowbrook", os.path.basename(out).lower())
        unpacked = os.path.join(self.work, "unpacked")
        with zipfile.ZipFile(out) as z:
            z.extractall(unpacked)
        a = reg.run(reg.files_in(src_dir), name="epic-mychart")
        b = reg.run(reg.files_in(unpacked), name="epic-mychart")
        self.assertEqual(counts(a), counts(b))
        for rel, text in texts(unpacked):
            self.assertNotIn("Rowan", text, rel)
            self.assertNotIn("Willowbrook", text, rel)

    def test_single_file(self):
        src = os.path.join(FIX, "csv", "excel_utf16", "excel_utf16.txt")
        dst = os.path.join(self.work, "labs.txt")
        run(src, dst)
        with open(dst, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xff\xfe"), "the encoding and byte-order mark are kept")
        self.assertIn("\tTriglycerides\t180\tmg/dL\t<150", raw.decode("utf-16"))

    def test_apple_health_owner_and_birth_date(self):
        src = os.path.join(self.work, "apple_health_export")
        os.makedirs(src)
        with open(os.path.join(src, "export.xml"), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE HealthData [\n<!ELEMENT HealthData ANY>\n]>\n'
                    '<HealthData locale="en_US">\n <ExportDate value="2024-03-13 09:00:00 +0100"/>\n'
                    ' <Me HKCharacteristicTypeIdentifierDateOfBirth="1980-04-02"/>\n'
                    ' <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Robin’s Apple Watch" unit="count"'
                    ' startDate="2024-03-09 10:00:00 -0500" endDate="2024-03-09 10:10:00 -0500" value="1000"/>\n'
                    '</HealthData>\n')
        os.makedirs(os.path.join(src, "workout-routes"))
        with open(os.path.join(src, "workout-routes", "route_2024-03-10.gpx"), "w") as f:
            f.write("<gpx/>")
        dst = os.path.join(self.work, "out")
        rep = run(src, dst)
        with open(os.path.join(dst, "export.xml"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("<!DOCTYPE HealthData [", text)
        self.assertRegex(text, r'DateOfBirth="\d{4}-01-01"')
        self.assertNotIn("Robin", text)
        self.assertRegex(text, r'sourceName="[A-Z][a-z]+’s Apple Watch"')
        (left, why), = rep["left_out"]   # named by its new name: the old one can hold a date or a name
        self.assertRegex(left, r"^workout-routes/route_\d{4}-\d\d-\d\d\.gpx$")
        self.assertNotIn("2024-03-10", left)
        self.assertIn("location track", why)
        self.assertFalse(os.path.exists(os.path.join(dst, "workout-routes")))
        self.assertEqual(reg.run(reg.files_in(dst), name="apple-health")["warnings"], [])


class Refusals(unittest.TestCase):
    """Formats it can't clean and unsafe input are refused, and nothing is written."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="anonymise-test-")
        self.addCleanup(shutil.rmtree, self.work, True)

    def refused(self, src, words):
        dst = os.path.join(self.work, "out")
        with self.assertRaises(A.Refused) as cm:
            A.anonymise(src, dst, seed=SEED)
        self.assertIn(words, str(cm.exception))
        self.assertFalse(os.path.exists(dst))
        self.assertEqual([n for n in os.listdir(self.work) if n.startswith(".anonymise-")], [])
        return str(cm.exception)

    def zip_with(self, members, name="bad.zip"):
        p = os.path.join(self.work, name)
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for info, data in members:
                z.writestr(info, data)
        return p

    def test_pdf(self):
        self.refused(os.path.join(FIX, "pdf-labs", "quest"), "can't clean PDFs")

    def test_spreadsheet(self):
        p = os.path.join(self.work, "claims.xlsx")
        with open(p, "wb") as f:
            f.write(b"PK\x03\x04 not really")
        self.refused(p, "Save a spreadsheet as CSV")

    def test_path_out_of_the_folder(self):
        self.refused(self.zip_with([("../evil.json", "{}")]), "goes up a folder")
        self.refused(self.zip_with([("/etc/evil.json", "{}")], "abs.zip"), "absolute path")

    def test_symlink(self):
        info = zipfile.ZipInfo("link.json")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.refused(self.zip_with([(info, "/etc/passwd")]), "symbolic link")

    def test_password_protected(self):
        p = self.zip_with([("a.json", "{}")])
        with open(p, "rb") as f:
            raw = bytearray(f.read())
        for sig in (b"PK\x03\x04", b"PK\x01\x02"):   # set the "encrypted" flag in both headers
            i = raw.find(sig)
            off = i + (6 if sig == b"PK\x03\x04" else 8)
            raw[off] |= 1
        with open(p, "wb") as f:
            f.write(raw)
        self.refused(p, "password-protected")

    def test_zip_bomb(self):
        self.refused(self.zip_with([("big.csv", "0," * (3 * 2 ** 20))]), "zip bomb")

    def test_zips_too_deep(self):
        inner = b'{"resourceType": "Patient"}'
        for i in range(4):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr(f"level{i}.zip" if i else "p.json", inner)
            inner = buf.getvalue()
        p = os.path.join(self.work, "deep.zip")
        with open(p, "wb") as f:
            f.write(inner)
        self.refused(p, "zips inside zips")

    def test_xml_entities(self):
        src = os.path.join(ROOT, "tests", "fixtures", "ccda", "billion-laughs.xml")
        self.refused(src, "Nothing could be written")
        folder = os.path.join(self.work, "export")
        os.makedirs(folder)
        shutil.copy(src, folder)
        shutil.copy(os.path.join(ROOT, "tests", "fixtures", "ccda", "external-entity.xml"), folder)
        shutil.copy(os.path.join(FIX, "csv", "us_comma", "us_comma.csv"), folder)
        rep = A.anonymise(folder, os.path.join(self.work, "copy"), seed=SEED)
        self.assertEqual(sorted(r for r, _ in rep["left_out"]), ["billion-laughs.xml", "external-entity.xml"])
        self.assertTrue(all("entities" in why for _, why in rep["left_out"]))
        self.assertEqual(sorted(os.listdir(os.path.join(self.work, "copy"))), ["us_comma.csv"])

    def test_hostile_files_left_out(self):
        """A JSON nested past what Python can read and a binary blob are left out, said so, and the rest copied."""
        folder = os.path.join(self.work, "Robin Example export")
        os.makedirs(folder)
        with open(os.path.join(folder, "deep.json"), "w") as f:
            f.write("[" * 200000 + "]" * 200000)
        with open(os.path.join(folder, "blob.bin"), "wb") as f:
            f.write(b"\0\1\2" * 100)
        with open(os.path.join(folder, "patient.json"), "w") as f:
            json.dump({"patient": {"firstName": "Robin", "lastName": "Example", "mrn": "MRN-448812"}}, f)
        rep = A.anonymise(folder, None, seed=SEED)
        self.assertEqual(sorted(r for r, _ in rep["left_out"]), ["blob.bin", "deep.json"])
        self.assertNotIn("Robin", rep["output"])
        text = rep["_report"].text(rep["output"])
        self.assertNotIn("Robin", text, "the report names files by their new names")
        with open(os.path.join(rep["output"], "patient.json")) as f:
            p = json.load(f)["patient"]
        self.assertNotIn(p["firstName"], ("Robin", ""))
        self.assertRegex(p["mrn"], r"^MRN-\d{6}$")
        self.assertNotEqual(p["mrn"], "MRN-448812")

    def test_output_not_over_anything(self):
        src = os.path.join(FIX, "csv", "us_comma")
        os.makedirs(os.path.join(self.work, "out"))
        with self.assertRaises(A.Refused):
            A.anonymise(src, os.path.join(self.work, "out"))
        folder = os.path.join(self.work, "export")
        shutil.copytree(src, folder)
        with self.assertRaises(A.Refused):
            A.anonymise(folder, os.path.join(folder, "copy"))

    def test_command_line(self):
        r = subprocess.run([sys.executable, "-I", TOOL, os.path.join(FIX, "pdf-labs", "quest")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("refused", r.stderr)
        dst = os.path.join(self.work, "copy")
        r = subprocess.run([sys.executable, "-I", TOOL, os.path.join(FIX, "ccda", "riverbend-visit"), "-o", dst,
                            "--seed", "x", "--jitter", "5"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Anonymised copy written to", r.stdout)
        self.assertIn("Automated cleaning misses things", r.stdout)
        self.assertTrue(os.listdir(dst))


class Pieces(unittest.TestCase):
    def setUp(self):
        self.an = A.Anonymiser(seed=SEED)
        self.off = self.an.offset

    def done(self, *texts):
        """Pass 1 over the texts, then pass 2: their cleaned versions."""
        for t in texts:
            self.an.text(t)
        self.an.finish_collecting()
        return [self.an.text(t) for t in texts]

    def moved(self, y, m, d):
        return date(y, m, d) + self.off

    def test_date_spellings(self):
        self.an.finish_collecting()
        n = self.moved(2026, 3, 2)
        cases = {
            "2026-03-02": f"{n:%Y-%m-%d}", "2026-03-02T08:30:00-06:00": f"{n:%Y-%m-%d}T08:30:00-06:00",
            "20260302083000-0500": f"{n:%Y%m%d}083000-0500", "03/02/2026": f"{n:%m/%d/%Y}",
            "3/2/26": f"{n.month}/{n.day}/{n:%y}", "02.03.2026": f"{n:%d.%m.%Y}", "Mar 02, 2026": f"{n:%b %d, %Y}",
            "March 2, 2026": f"{n:%B} {n.day}, {n:%Y}", "2 March 2026": f"{n.day} {n:%B %Y}",
            "02-Mar-2026": f"{n:%d-%b-%Y}", "MAR 02, 2026 7:40 AM": f"{n:%b %d, %Y}".upper() + " 7:40 AM",
            "2024-03-09 10:00:00 -0500": f"{self.moved(2024, 3, 9):%Y-%m-%d} 10:00:00 -0500",
        }
        for orig, want in cases.items():
            self.assertEqual(self.an.date_value(orig), want, orig)
        self.assertEqual(self.an.hl7("2010"), str((date(2010, 7, 1) + self.off).year))
        self.assertEqual(self.an.date_value("04/31/2025"), "04/31/2025")   # not a real date: kept, and reported
        self.assertEqual(self.an.date_value("13.1"), None)
        self.assertEqual(self.an.date_value("135 - 145"), None)
        self.assertEqual(self.off.days % 7, 0)
        self.assertEqual(date(2026, 3, 2).weekday(), n.weekday())

    def test_day_first_when_the_export_says_so(self):
        for t in ("31/01/2025", "05/02/2025"):
            self.an.text(t)
        self.an.finish_collecting()
        n = self.moved(2025, 2, 5)
        self.assertEqual(self.an.text("05/02/2025"), f"{n:%d/%m/%Y}")

    def test_free_text(self):
        note = ("Dr. Jane Smith (Lakeside Clinic) called Pat Smith, RN on 555-123-4567 about MRN 00123456 and "
                "SSN 123-45-6789; reply to jane.smith@example.com. Member ID: XK4471920. Seen March 3, 2026.")
        out, again = self.done(note, "Smith said hello")
        for s in ("Jane", "Smith", "Lakeside", "555-123-4567", "00123456", "123-45-6789", "jane.smith",
                  "XK4471920", "March 3, 2026", "Pat"):
            self.assertNotIn(s, out)
        for s in ("Dr. ", "Clinic", ", RN on ", "MRN ", "SSN ", "Member ID: ", "@example.org"):
            self.assertIn(s, out)
        self.assertNotIn("Smith", again, "a surname found once is replaced everywhere")
        self.assertEqual(out, self.an.text(note), "the same text gives the same result")

    def test_identifiers(self):
        self.an.finish_collecting()

        def shape(s):
            return re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "0", s))
        for orig in ("PX-26-0001", "BC2026041100071", "MRN000123", "FICTIONAL-MBI-0001"):
            fake = self.an.ident(orig)
            self.assertNotEqual(fake, orig)
            self.assertEqual(shape(fake), shape(orig), "the same shape")
            self.assertEqual(self.an.ident(orig), fake, "the same everywhere")
        uuid = self.an.ident("3f2a9c1e-77b0-4c1d-9e2f-0a1b2c3d4e5f")
        self.assertRegex(uuid, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        self.assertNotEqual(uuid, "3f2a9c1e-77b0-4c1d-9e2f-0a1b2c3d4e5f")
        self.assertTrue(self.an.ident("PX-26-0001").startswith("PX-"), "a short prefix says what kind it is")
        self.assertEqual(self.an.ident("e1"), "e1", "short keys are kept")
        npi = self.an.ident("1234567893", kind="npi")
        digits = [int(c) for c in "80840" + npi]
        total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(reversed(digits)))
        self.assertEqual(total % 10, 0, "a made-up NPI still has a valid check digit")

    def test_names(self):
        an = self.an
        for s in ("Ellis, Rowan J", "DR. AVERY LIN", "Jordan Pike, RN"):
            an.person(s)
        an.finish_collecting()
        self.assertRegex(an.person("Ellis, Rowan J"), r"^[A-Z][a-z]+, [A-Z][a-z]+ [A-Z]$")
        self.assertRegex(an.person("DR. AVERY LIN"), r"^DR\. [A-Z]+ [A-Z]+$")
        self.assertTrue(an.person("Jordan Pike, RN").endswith(", RN"))
        self.assertNotIn("Ellis", an.text("Ask Rowan Ellis"))
        self.assertTrue(an.looks_like_org("MAPLE GROVE MEDICAL CENTER"))
        self.assertFalse(an.looks_like_org("Dr. Rowan Ellis"))
        self.assertTrue(an.org("Valley Reference Laboratory").endswith(" Reference Laboratory"))

    def test_placeholders(self):
        self.assertTrue(A._pdf().startswith(b"%PDF-1.4") and A._pdf().rstrip().endswith(b"%%EOF"))
        self.assertTrue(A._png().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(A._jpeg().startswith(b"\xff\xd8") and A._jpeg().endswith(b"\xff\xd9"))
        self.assertTrue(A.GIF.startswith(b"GIF89a") and len(A.GIF) == 43)

    def test_standard_library_only(self):
        with open(TOOL, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(mods - set(sys.stdlib_module_names), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
