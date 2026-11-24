#!/usr/bin/env python3
"""Epic MyChart importer tests (#28): the plug-in gives what build.py gives for the same export, and its own edge cases.

The comparison reads the fictional exports in tests/fixtures/build/ (mychart-20261101/, otherclinic-20261020/) as one
upload and checks the result against tests/fixtures/build/expected.json, which tests/test_importers.py checks is
build.py's output. The differences allowed are listed in BuildComparison; everything else must match.

  python -m unittest tests.test_importer_epic_mychart -v
"""
import collections, copy, json, os, shutil, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "tests", "fixtures", "build")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "importers", "epic-mychart", "willowbrook-export")
sys.path.insert(0, ROOT)

from server import importers as reg   # noqa: E402
from server.importers import common, epic_mychart as epic   # noqa: E402


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def copy_tree(src, dst):
    shutil.copytree(src, dst)
    return dst


class Tmp(unittest.TestCase):
    def tmp(self):
        d = tempfile.mkdtemp(prefix="sanguity-epic-")
        self.addCleanup(shutil.rmtree, d, True)
        return d


# ------------------------------------------------------------------ the same results as build.py

def _series_group(name, unit):
    """Which chart a series is: its canonical test and its unit. Lets build.py's "Sodium" (primary export, named as
    reported) and "Sodium, Blood" (other export) meet the plug-in's single canonical "Sodium, Blood" series, and
    "Hemoglobin (Other Clinic)" meet "Hemoglobin (g/L)"."""
    base = name[:-len(name.rsplit(" (", 1)[-1]) - 2] if name.endswith(")") and " (" in name and not name.endswith("(External)") else name
    canon = common.canonical_lab(base) or base
    return canon, common.unit_for(canon, unit)


class BuildComparison(unittest.TestCase):
    """The plug-in reads the build fixture as build.py does. Differences allowed, each applied to build.py's side below:

    1. Event ids and the new "key" fields: ids are numbered per import; keys are new.
    2. Document URLs point into the upload ("mychart-20261101/documents/...") instead of build.py's served
       "records/<folder>/..." prefix.
    3. Series names: the plug-in names every export's series canonically (build.py does it only for the extra
       exports), so the primary's "Sodium" joins the other export's "Sodium, Blood" (mEq/L and mmol/L are one unit
       for sodium), and a test in other units is "<test> (<unit>)" ("Hemoglobin (g/L)") when the export doesn't name
       its organisation. Series are compared chart by chart: the same points, in the same canonical series and unit.
    4. Organisation names and tags that build.py takes from sources.json: "Other Clinic" and its tag "OC" (the
       other export has no visit records, so nothing in it names its organisation), and "Example Health System" on
       the clinic weight (the primary's visit record has no custodian).
    5. Visit details: build.py's {"type": "visit", **visit} let the encounter type overwrite "type" (so the app's
       drawer showed nothing); the plug-in uses the C-CDA importer's visit details. Who and where must match.
    """

    @classmethod
    def setUpClass(cls):
        cls.conf = load(os.path.join(BUILD, "sources.json"))
        cls.build = load(os.path.join(BUILD, "expected.json"))
        files = reg.files_in(BUILD)
        cls.files = files
        cls.hit = reg.match(files)
        cls.out = epic.parse(files)

    # ---- one view of both outputs, with the allowed differences taken out of build.py's side
    def _unconfigured(self):
        """(org, tag) pairs that build.py has only from sources.json (difference 4)."""
        return [(s.get("org", ""), s.get("tag", "")) for s in self.conf.get("epic", [])]

    def _build_side(self):
        b = copy.deepcopy(self.build)
        for org, tag in self._unconfigured():
            for e in b["events"]:
                for f in ("title", "sub"):
                    e[f] = e[f].replace(f" · {org}", "")
                if e.get("src") == tag:
                    del e["src"]
            for s in b["labs"]:
                s["pts"] = [p[:6] if len(p) > 6 and p[6] == tag else p for p in s["pts"]]
        for d in b["details"].values():
            if d.get("type") == "file":                                   # difference 2
                d["url"] = d["url"].split("/", 1)[1] if d["url"].startswith("records/") else d["url"]
        for e in b["events"]:                                             # difference 5
            if e["k"] == "visit":
                d = b["details"][str(e["id"])]
                b["details"][str(e["id"])] = {"type": "visit", "who": d["who"], "where": d["where"]}
        b["clinicWeights"] = [w[:2] for w in b["clinicWeights"]]         # difference 4: the weight's organisation
        return b

    def _plugin_side(self):
        p = copy.deepcopy(json.loads(json.dumps(self.out["data"])))
        for e in p["events"]:
            if e["k"] == "visit":
                d = p["details"][str(e["id"])]
                p["details"][str(e["id"])] = {"type": "visit", "who": d["who"], "where": d["where"]}
        p["clinicWeights"] = [w[:2] for w in p["clinicWeights"]]
        return p

    @staticmethod
    def _events(data):
        """Events without ids and keys (difference 1), each with its details; a lab day with the events it lists."""
        by_id = {e["id"]: e for e in data["events"]}

        def view(e):
            v = {k: x for k, x in e.items() if k not in ("id", "key")}
            d = data["details"].get(str(e["id"]))
            if d and d.get("type") == "labday":
                d = {"type": "labday", "items": sorted(json.dumps(view(by_id[i]), sort_keys=True) for i in d["ids"])}
            v["detail"] = d
            return v
        return sorted(json.dumps(view(e), sort_keys=True, ensure_ascii=False) for e in data["events"])

    @staticmethod
    def _charts(data):
        charts = collections.defaultdict(list)
        for s in data["labs"]:
            charts[_series_group(s["name"], s["unit"])].append(s)
        return charts

    # ---- the checks
    def test_the_registry_picks_this_importer(self):
        self.assertEqual(self.hit["importer"].NAME, "epic-mychart")
        self.assertEqual(self.hit["tied"], [])
        self.assertEqual(reg.check(self.out, self.files), [])
        self.assertEqual(self.out["warnings"], [])

    def test_events_and_details(self):
        self.assertEqual(self._events(self._plugin_side()), self._events(self._build_side()))

    def test_every_details_entry_belongs_to_an_event(self):
        self.assertEqual(len(self.out["data"]["details"]), len(self.build["details"]))

    def test_lab_series(self):
        built, plugin = self._charts(self._build_side()), self._charts(self._plugin_side())
        self.assertEqual(sorted(plugin), sorted(built))
        for chart in built:
            b, p = built[chart], plugin[chart]
            with self.subTest(chart=chart):
                self.assertEqual(len(p), 1)
                self.assertEqual(sorted(p[0]["pts"]), sorted(pt for s in b for pt in s["pts"]))
                self.assertEqual(sorted(p[0]["txt"]), sorted(t for s in b for t in s["txt"]))
                if len(b) == 1:          # nothing merged: the whole series is the same, bar its name and key
                    self.assertEqual({k: v for k, v in p[0].items() if k not in ("name", "key")},
                                     {k: v for k, v in b[0].items() if k != "name"})

    def test_series_names(self):
        """Difference 3, spelled out: build.py's names where they were canonical already, canonical ones otherwise."""
        self.assertEqual(sorted(s["name"] for s in self.out["data"]["labs"]),
                         sorted(["Hemoglobin", "Sodium, Blood", "Potassium, Blood", "WBC", "Hemoglobin (g/L)",
                                 "Platelets (External)", "Blood culture"]))
        same = {s["name"] for s in self.build["labs"]} & {s["name"] for s in self.out["data"]["labs"]}
        self.assertEqual(same, {"Hemoglobin", "Sodium, Blood", "Potassium, Blood", "WBC", "Platelets (External)", "Blood culture"})

    def test_clinic_weights(self):
        self.assertEqual(self._plugin_side()["clinicWeights"], self._build_side()["clinicWeights"])
        self.assertEqual([w[2] for w in self.out["data"]["clinicWeights"]], [""])   # no custodian, so no organisation

    def test_no_name_comes_from_sources_json(self):
        text = json.dumps(self.out, ensure_ascii=False)
        for s in [self.conf["primary"]] + self.conf.get("epic", []):
            self.assertNotIn(s["org"], text)
            if s.get("tag"):
                self.assertNotIn(f'"{s["tag"]}"', text)

    def test_document_urls_are_in_the_upload(self):
        urls = sorted(d["url"] for d in self.out["data"]["details"].values() if d["type"] == "file")
        self.assertEqual(urls, ["mychart-20261101/documents/visit1/2026-01-10_Visit_Summary_1.pdf",
                                "otherclinic-20261020/documents/2026-04-01_Discharge_Note.pdf"])
        self.assertTrue(all(u in self.files for u in urls))


# ------------------------------------------------------------------ detect

class Detect(Tmp):
    def test_epic_exports(self):
        self.assertEqual(epic.detect(reg.files_in(FIXTURE)), 1.0)
        self.assertEqual(epic.detect(reg.files_in(BUILD)), 1.0)
        self.assertEqual(reg.match(reg.files_in(FIXTURE))["importer"].NAME, "epic-mychart")

    def test_a_plain_ccda_upload_is_left_to_ccda(self):
        for case in ("maple-grove-ccd", "riverbend-visit"):
            files = reg.files_in(os.path.join(ROOT, "tests", "fixtures", "importers", "ccda", case))
            self.assertEqual(epic.detect(files), 0.0)
            self.assertEqual(reg.match(files)["importer"].NAME, "ccda")
        d = self.tmp()   # even in a folder called visit-records/
        os.makedirs(os.path.join(d, "visit-records"))
        shutil.copy(os.path.join(ROOT, "tests", "fixtures", "importers", "ccda", "maple-grove-ccd", "maple-grove-ccd.xml"),
                    os.path.join(d, "visit-records", "summary.xml"))
        self.assertEqual(epic.detect(reg.files_in(d)), 0.0)
        self.assertEqual(reg.match(reg.files_in(d))["importer"].NAME, "ccda")

    def test_without_test_results_it_still_beats_ccda_on_the_visit_records(self):
        d = copy_tree(FIXTURE, os.path.join(self.tmp(), "export"))
        shutil.rmtree(os.path.join(d, "test-results"))
        z = os.path.join(d, "visit-records", "willowbrook_visit_records.zip")
        with zipfile.ZipFile(z) as zf:
            loose = zf.read("IHE_XDM/SUBSET01/DOC0001.XML")
        with open(os.path.join(d, "visit-records", "DOC0001.XML"), "wb") as f:
            f.write(loose)
        files = reg.files_in(d)
        self.assertEqual(epic.detect(files), 0.97)
        self.assertEqual(reg.match(files)["importer"].NAME, "epic-mychart")

    def test_other_formats(self):
        for case in ("export", "folder"):
            self.assertEqual(epic.detect(reg.files_in(os.path.join(ROOT, "tests", "fixtures", "importers", "sanguity", case))), 0.0)
        self.assertEqual(epic.detect(reg.files_in(os.path.join(ROOT, "tests", "fixtures", "importers", "fhir", "bundle"))), 0.0)
        self.assertEqual(epic.detect({}), 0.0)

    def test_a_results_file_that_is_not_json_scores_low(self):
        d = self.tmp()
        os.makedirs(os.path.join(d, "test-results"))
        with open(os.path.join(d, "test-results", "x_test_results_raw.json"), "w") as f:
            f.write("not json")
        self.assertLess(epic.detect(reg.files_in(d)), 0.5)


# ------------------------------------------------------------------ parse

class Parse(Tmp):
    def test_names_come_from_the_exports_own_metadata(self):
        data = epic.parse(reg.files_in(FIXTURE))["data"]
        org = "Willowbrook Health System"    # the C-CDA visit records' custodian
        visible = [e for e in data["events"] if e["k"] not in ("letter",)]
        self.assertTrue(all(org in e["sub"] or org in e["title"] for e in visible), [e["sub"] for e in visible])
        self.assertEqual({e.get("src") for e in data["events"] if e["k"] == "lab"}, {org})
        self.assertTrue(all(p[6] in (org, "EXT") for s in data["labs"] for p in s["pts"]))
        self.assertEqual({w[2] for w in data["clinicWeights"]}, {org})
        letter = next(e for e in data["events"] if e["k"] == "letter")
        self.assertEqual(letter["sub"], "Willowbrook Hematology")          # its department, from the letters file
        self.assertEqual(letter["title"], "Insurance authorization: Bone marrow biopsy (AUTH-55120)")

    def test_visit_records_in_a_zip(self):
        data = epic.parse(reg.files_in(FIXTURE))["data"]
        visits = [e for e in data["events"] if e["k"] == "visit"]
        self.assertEqual([(v["t"], v["title"]) for v in visits],
                         [("2026-02-14T07:30:00-06:00", "Outpatient visit"), ("2026-03-08T20:15:00-05:00", "Hospital admission")])
        self.assertEqual([v["key"] for v in visits], ["epic-mychart:1.2.840.114350.1.13.999.2.7.3.698084.8^4400123",
                                                       "epic-mychart:1.2.840.114350.1.13.999.2.7.3.698084.8^4400188"])
        stays = [e for e in data["events"] if e["k"] == "stay"]
        # admissions.json has the stay, so none is guessed from inpatient results, and the C-CDA admission adds none
        self.assertEqual([(s["t"], data["details"][s["id"]]["how"]) for s in stays], [("2026-03-08T00:00:00", "Visit list")])

    @staticmethod
    def _vitals_record(*sets):
        """A fictional visit record whose Vital Signs section holds one organizer per (time, {LOINC: (value, unit)})."""
        orgs = "".join(
            f'<entry><organizer classCode="CLUSTER" moodCode="EVN"><effectiveTime value="{t}"/>'
            + "".join(f'<component><observation classCode="OBS" moodCode="EVN"><code code="{c}" codeSystem="2.16.840.1.113883.6.1"/>'
                      f'<value xsi:type="PQ" value="{v}" unit="{u}"/></observation></component>' for c, (v, u) in obs.items())
            + "</organizer></entry>" for t, obs in sets)
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<ClinicalDocument xmlns="urn:hl7-org:v3" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><templateId root="2.16.840.1.113883.10.20.22.1.1"/>'
                '<custodian><assignedCustodian><representedCustodianOrganization><name>Willowbrook Health System</name>'
                '</representedCustodianOrganization></assignedCustodian></custodian><component><structuredBody><component>'
                '<section><templateId root="2.16.840.1.113883.10.20.22.2.4.1"/><code code="8716-3"/><title>Vital Signs</title>'
                f'{orgs}</section></component></structuredBody></component></ClinicalDocument>')

    def test_vital_signs_from_the_visit_records(self):
        d = copy_tree(FIXTURE, os.path.join(self.tmp(), "export"))
        morning = ("20260214074500-0600", {"8480-6": (128, "mm[Hg]"), "8462-4": (82, "mm[Hg]"), "8867-4": (74, "/min"),
                                           "8310-5": (37.0, "Cel"), "59408-5": (98, "%"), "9279-1": (16, "/min")})
        afternoon = ("20260214151000-0600", {"8480-6": (128, "mm[Hg]"), "8462-4": (80, "mm[Hg]")})
        for name, sets in (("DOCV1.XML", [morning]), ("DOCV2.XML", [morning, afternoon])):   # each repeats the morning's
            with open(os.path.join(d, "visit-records", name), "w", encoding="utf-8") as f:
                f.write(self._vitals_record(*sets))
        out = epic.parse(reg.files_in(d))
        self.assertEqual(reg.check(out, reg.files_in(d)), [])
        labs = {s["name"]: s for s in out["data"]["labs"]}
        org = "Willowbrook Health System"
        sys_bp = labs["Blood pressure, systolic"]
        # the morning's reading once though both records carry it; the afternoon's, the same value, is a reading of its own
        self.assertEqual([p[:2] for p in sys_bp["pts"]], [["2026-02-14T07:45:00-06:00", 128], ["2026-02-14T15:10:00-06:00", 128]])
        self.assertEqual([p[1] for p in labs["Blood pressure, diastolic"]["pts"]], [82, 80])
        self.assertEqual((sys_bp["unit"], sys_bp["panel"], sys_bp["key"]), ("mmHg", "Vitals", "loinc:8480-6"))
        self.assertEqual({p[6] for s in labs.values() if s["panel"] == "Vitals" for p in s["pts"]}, {org})
        self.assertEqual(sys_bp["pts"][0][5], "128 mm[Hg]")                       # as reported
        self.assertEqual([p[1] for p in labs["Temperature"]["pts"]], [98.6])      # 37.0 °C
        for name in ("Heart rate (clinic)", "SpO2 (clinic)", "Respiratory rate (clinic)"):
            self.assertEqual(len(labs[name]["pts"]), 1, name)
        self.assertEqual(len(out["data"]["clinicWeights"]), 2)                  # weights as before, not as a series
        again = epic.parse(reg.files_in(d))
        self.assertEqual([s["key"] for s in out["data"]["labs"]], [s["key"] for s in again["data"]["labs"]])

    def test_long_conversations_are_read_whole(self):
        data = epic.parse(reg.files_in(FIXTURE))["data"]
        conv = next(e for e in data["events"] if e["title"] == "Question about my white count")
        self.assertEqual(conv["sub"], "2 messages · Dr. Rowan Ellis · Willowbrook Health System")
        self.assertEqual([m["from"] for m in data["details"][conv["id"]]["messages"]], ["You", "Dr. Rowan Ellis"])
        kinds = {e["title"]: e["k"] for e in data["events"] if e["k"].endswith("msg") or e["k"] == "message"}
        self.assertEqual(kinds, {"Question about my white count": "message", "Appointment reminder": "appt_msg", "(no subject)": "auto_msg"})

    def test_keys_are_stable(self):
        a = epic.parse(reg.files_in(FIXTURE))
        d = copy_tree(FIXTURE, os.path.join(self.tmp(), "somewhere", "else"))
        b = epic.parse(reg.files_in(os.path.dirname(os.path.dirname(d))))
        self.assertEqual([e["key"] for e in a["data"]["events"]], [e["key"] for e in b["data"]["events"]])
        self.assertEqual([s["key"] for s in a["data"]["labs"]], [s["key"] for s in b["data"]["labs"]])
        self.assertTrue(all(e["key"].startswith("epic-mychart:") for e in a["data"]["events"]))
        self.assertEqual(reg.check(a, reg.files_in(FIXTURE)), [])

    def test_a_second_export_in_other_units_gets_its_own_series(self):
        up = self.tmp()
        copy_tree(FIXTURE, os.path.join(up, "a-willowbrook"))
        copy_tree(os.path.join(BUILD, "otherclinic-20261020"), os.path.join(up, "b-otherclinic"))
        out = epic.parse(reg.files_in(up))
        self.assertEqual(reg.check(out, reg.files_in(up)), [])
        names = {s["name"]: s for s in out["data"]["labs"]}
        self.assertEqual(names["Hemoglobin (g/L)"]["unit"], "g/L")       # the other export names no organisation
        self.assertEqual(names["Hemoglobin (g/L)"]["key"], "loinc:718-7|g/L")
        self.assertEqual(names["Hemoglobin"]["key"], "loinc:718-7|g/dL")
        self.assertEqual(len(names["Sodium, Blood"]["pts"]), 3)          # mEq/L and mmol/L: one chart
        labdays = [e["key"] for e in out["data"]["events"] if e["k"] == "lab"]
        self.assertEqual(len(labdays), len(set(labdays)))

    def test_a_range_given_as_text_alone(self):
        """#836: a component whose range is only formatted ("<16.0", no low or high) is read through the one range
        parser (common.range_text), its edge kept: it was dropped, no range and no flag on Labs while the drawer showed
        the text beside the value. The row keeps the text as formatted."""
        got = []
        for value, text in (("16.0", "<16.0"), ("16.0", "less than 16.0"), ("16.0", "<=16.0"), ("12.0", ">12.0"), ("12.0", "≥ 12.0"),
                            ("11.2", "12.0-16.0 g/dL"), ("13", "See note")):
            up = self.tmp()
            copy_tree(FIXTURE, os.path.join(up, "export"))
            p = os.path.join(up, "export", "test-results", "willowbrook_test_results_raw.json")
            doc = load(p)
            hgb = doc["details"]["a1"]["results"][0]["resultComponents"][0]   # HGB, 13.1 against 12.0 - 16.0
            self.assertEqual(hgb["componentInfo"]["name"], "HGB")
            hgb["componentResultInfo"] = {"value": value, "numericValue": float(value), "referenceRange": {"formattedReferenceRange": text}}
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            out = epic.parse(reg.files_in(up))
            pt = next(q for s in out["data"]["labs"] if s["name"] == "Hemoglobin" for q in s["pts"] if q[0].startswith("2026-02-14"))
            row = next(r for e in out["data"]["details"].values() if isinstance(e, dict) for r in e.get("rows") or [] if r[0] == "HGB" and r[1] == value)
            got.append([pt[2], pt[3], pt[4], (pt[7:] or [""])[0], row[3]])
        self.assertEqual(got, [[None, 16, "H", "<", "<16.0"], [None, 16, "H", "<", "less than 16.0"], [None, 16, "", "", "<=16.0"],
                               [12, None, "L", ">", ">12.0"], [12, None, "", "", "≥ 12.0"], [12, 16, "L", "", "12.0-16.0 g/dL"],
                               [None, None, "", "", "See note"]])

    def test_damaged_visit_records_are_skipped_with_a_warning(self):
        d = copy_tree(FIXTURE, os.path.join(self.tmp(), "export"))
        with open(os.path.join(d, "visit-records", "broken.zip"), "wb") as f:
            f.write(b"PK\x03\x04 not really a zip")
        with zipfile.ZipFile(os.path.join(d, "visit-records", "hostile.zip"), "w") as z:
            z.writestr("DOC.XML", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
                                  '<ClinicalDocument xmlns="urn:hl7-org:v3">&a;</ClinicalDocument>')
        with open(os.path.join(d, "visit-records", "cut.xml"), "w") as f:
            f.write('<ClinicalDocument xmlns="urn:hl7-org:v3"><component>')
        out = epic.parse(reg.files_in(d))
        w = " ".join(out["warnings"])
        self.assertIn("broken.zip couldn't be unpacked", w)
        self.assertIn("DOCTYPE or ENTITY", w)
        self.assertIn("cut.xml is not well-formed XML", w)
        self.assertEqual(len([e for e in out["data"]["events"] if e["k"] == "visit"]), 2)   # the good ones still read

    def test_a_damaged_json_file_is_left_out(self):
        d = copy_tree(FIXTURE, os.path.join(self.tmp(), "export"))
        with open(os.path.join(d, "letters", "willowbrook_letters.json"), "w") as f:
            f.write("{ cut short")
        out = epic.parse(reg.files_in(d))
        self.assertTrue(any(w.startswith("letters/willowbrook_letters.json couldn't be read") for w in out["warnings"]))
        self.assertFalse(any(e["k"] == "letter" for e in out["data"]["events"]))
        self.assertTrue(any(e["k"] == "lab" for e in out["data"]["events"]))

    def test_nothing_readable(self):
        with self.assertRaisesRegex(ValueError, "doesn't look like an Epic MyChart export"):
            epic.parse({"notes.txt": os.path.join(FIXTURE, "documents", "README.txt")})
        d = self.tmp()
        os.makedirs(os.path.join(d, "test-results"))
        with open(os.path.join(d, "test-results", "x_test_results_raw.json"), "w") as f:
            f.write("[1, 2")
        with self.assertRaisesRegex(ValueError, "Nothing in the Epic MyChart export could be read"):
            epic.parse(reg.files_in(d))

    def test_upload_through_the_registry(self):
        out = reg.run(reg.files_in(FIXTURE))
        self.assertEqual((out["importer"], out["label"]), ("epic-mychart", "Epic MyChart export"))


if __name__ == "__main__":
    unittest.main()
