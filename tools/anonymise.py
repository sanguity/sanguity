#!/usr/bin/env python3
"""Make an anonymised copy of a health-records export, to share as a sample for an importer (#34).

    python3 tools/anonymise.py EXPORT [-o OUTPUT] [--seed TEXT] [--jitter PERCENT]
                               [--keep-notes] [--keep-organisations]

EXPORT is a file, a folder or a zip in the formats Sanguity's importers read, or close to them: C-CDA and other
XML, FHIR JSON bundles, resources and NDJSON, Apple Health's export.xml and clinical-records, lab and claims CSV,
insurers' claims JSON, Epic MyChart and CareSpace exports. The copy keeps the folders, file and field names, codes,
units, values and reference ranges, so it still imports, and changes:

  - names of people (patients, relatives, clinicians), consistently: the same name gets the same made-up name;
  - names of clinics, hospitals, labs and insurers, keeping words such as "Clinic" or "Pharmacy" (unless
    --keep-organisations);
  - identifiers (record, member, claim and order numbers, SSN-like numbers, NPIs, resource ids), with made-up
    ones of the same shape, the same everywhere they appear;
  - addresses, phone numbers and email addresses;
  - every date, moved back by one random number of whole weeks (the same for the whole run, so order, gaps and
    weekdays stay as they were); birth dates become 1 January of their (moved) year;
  - notes and messages (free text written about the person) replaced with "Text removed." (unless --keep-notes,
    which keeps them with the names, numbers and dates above cleaned out of them);
  - documents (PDFs, pictures, scans) replaced with placeholders; location tracks (workout routes) left out;
  - with --jitter, lab and vital-sign values moved by up to that many percent.

It reports what it changed and what it couldn't be sure of. Automated cleaning misses things, free text above all:
open the copy and look it over before sharing it (docs/anonymising-exports.md says what to check).

--seed makes a run repeatable (the same input and seed give the same copy); keep the seed private, since anyone
with it and a guess at the original can check the guess. Without it every run picks a new random key.

Standard library only, and the input is treated as hostile: zip limits (files, sizes, unpack ratio, nesting), no
paths outside the copy, no symbolic links, no XML entity declarations. Nothing is sent anywhere.
Exit status: 0 done, 2 refused (a format it can't clean, or unsafe input), 1 an unexpected error.
"""
import argparse, base64, collections, csv, hashlib, hmac, io, json, os, random, re, secrets, shutil, stat, struct
import sys, tempfile, unicodedata, zipfile, zlib
import xml.parsers.expat
from datetime import date, timedelta

# ------------------------------------------------------------------ limits (as the server's uploads; see server/config.py)
MB, GB = 2 ** 20, 2 ** 30
MAX_FILES = 20000          # files in the whole input, zips unpacked
MAX_FILE = 4 * GB          # one file, unpacked
MAX_TOTAL = 16 * GB        # the whole input, unpacked
MAX_RATIO, RATIO_FROM = 200, MB   # a zip member may unpack to at most 200 times its packed size (beyond 1 MB)
MAX_DEPTH = 3              # zips inside zips
MAX_WHOLE = 512 * MB       # JSON, CSV and text files are read whole; XML is streamed
MACOS_FORKS = "__MACOSX/"

REMOVED = "Text removed."
PLACEHOLDER_TEXT = "Document removed by anonymise.py"


class Refused(Exception):
    """The input can't be cleaned (or isn't safe to read): a plain-language reason, exit status 2."""


class Skip(Exception):
    """One file can't be read: it is left out of the copy, with this reason in the report."""


# ------------------------------------------------------------------ word lists

GIVEN = ["Ari", "Blake", "Cam", "Dana", "Eden", "Finley", "Gray", "Harper", "Indy", "Jesse", "Kai", "Lee", "Mika",
         "Noor", "Oakley", "Parker", "Reese", "Sage", "Tatum", "Uma", "Wren", "Yael", "Zion", "Ashton", "Bellamy",
         "Cary", "Devon", "Emery", "Frankie", "Hollis", "Jules", "Kendall", "Lennox", "Marlow", "Nico", "Peyton",
         "Remy", "Sasha", "Teagan", "Lux", "Rio", "Shay", "Toby", "Vic", "Wynn", "Zuri", "Arlo", "Bo", "Cleo", "Dov"]
FAMILY_A = ["Ash", "Bram", "Cal", "Dun", "El", "Fen", "Gar", "Hal", "Ing", "Kel", "Lar", "Mor", "Nor", "Os", "Pen",
            "Ral", "Sel", "Thorn", "Wex", "Yar"]
FAMILY_B = ["by", "croft", "dale", "field", "ford", "gate", "holm", "ley", "more", "stead", "ton", "well", "wick",
            "worth", "bury", "combe", "den", "hurst", "mere", "shaw"]
PLACE_A = ["Alder", "Birch", "Cedar", "Elm", "Hazel", "Larch", "Linden", "Aspen", "Hawthorn", "Holly", "Ivy",
           "Laurel", "Myrtle", "Oak", "Pine", "Sorrel", "Thistle", "Yew", "Bracken", "Fennel"]
PLACE_B = ["brook", "field", "ford", "gate", "haven", "hill", "mead", "mont", "stone", "wood", "crest", "cliff",
           "bank", "marsh", "moor", "ridge", "wold", "holt", "stead", "dene"]

PREFIXES = {"dr", "mr", "mrs", "ms", "mx", "miss", "prof", "sir", "dame", "rev", "fr", "dear"}
CREDENTIALS = {"md", "do", "rn", "np", "pa", "pa-c", "phd", "dds", "dmd", "mbbs", "fnp", "fnp-bc", "aprn", "cnp", "cnm",
               "lpn", "dpm", "pharmd", "lcsw", "dpt", "crna", "arnp", "msn", "bsn", "facp", "facs", "jr", "sr", "ii",
               "iii", "iv", "esq", "od", "pt", "ot", "slp", "rd", "cna", "ma", "mph", "mba", "frcp", "mrcp"}
# words that are never a person's name to replace (they turn up after "Dear", before "MD" and in labels)
STOP = {"patient", "patients", "doctor", "nurse", "team", "staff", "member", "members", "family", "friend", "sir",
        "madam", "all", "care", "clinic", "health", "office", "lab", "labs", "results", "result", "test", "tests",
        "the", "and", "of", "for", "to", "from", "with", "unknown", "none", "null", "provider", "providers",
        "customer", "colleague", "colleagues", "parent", "guardian", "visit", "new", "primary", "your", "my", "our",
        "his", "her", "their", "a", "an", "on", "in", "at", "by", "or", "not", "no", "yes", "is", "was", "be",
        "please", "thank", "thanks", "hello", "hi", "dear", "regards", "sincerely", "mychart", "portal", "message",
        "messages", "letter", "report", "summary", "note", "notes", "record", "records", "chart", "general",
        "medical", "medicine", "hospital", "center", "centre", "department", "service", "services", "group",
        "plan", "insurance", "pharmacy", "laboratory", "imaging", "radiology", "emergency", "urgent", "internal",
        "attending", "physician", "surgeon", "specialist", "on-call", "oncall", "covering", "resident", "fellow",
        "user", "admin", "system", "support", "sample", "fictional", "test", "name", "names", "export"}
ORG_WORDS = {"hospital", "hospitals", "clinic", "clinics", "center", "centre", "centers", "medical", "medicine",
             "health", "healthcare", "care", "lab", "labs", "laboratory", "laboratories", "diagnostics", "imaging",
             "radiology", "pharmacy", "pharmacies", "drug", "drugs", "group", "associates", "partners", "practice",
             "physicians", "specialists", "services", "service", "system", "systems", "network", "institute",
             "university", "college", "foundation", "department", "dept", "unit", "ward", "wing", "office", "inc",
             "llc", "ltd", "pc", "pllc", "llp", "corp", "company", "plan", "plans", "insurance", "insurer", "mutual",
             "mychart", "portal", "ehr", "therapy", "physical", "surgical", "surgery", "oncology", "cardiology",
             "hematology", "haematology", "dermatology", "pediatrics", "paediatrics", "orthopedics", "orthopaedics",
             "neurology", "urology", "gastroenterology", "pulmonology", "rheumatology", "endocrinology", "obstetrics",
             "gynecology", "psychiatry", "family", "internal", "emergency", "urgent", "general", "regional",
             "community", "memorial", "county", "state", "national", "children's", "childrens", "women's", "womens",
             "infusion", "cancer", "heart", "eye", "dental", "dentistry", "vision", "rehab", "rehabilitation",
             "wellness", "reference", "pathology", "nursing", "home", "hospice", "the", "of", "and", "at", "for",
             "main", "north", "south", "east", "west", "suite", "floor", "building", "campus", "outpatient",
             "inpatient", "ambulatory", "primary", "senior", "behavioral", "mental", "sleep", "spine", "vascular",
             "kidney", "dialysis", "transplant", "maternity", "birth", "blood", "bank", "mri", "ct", "x-ray",
             "xray", "ultrasound", "screening", "testing", "specialty", "medicare", "medicaid", "blue", "cross",
             "shield", "health-system", "healthsystem", "hmo", "ppo", "epo", "pos", "advantage", "part", "supplement",
             "prescription", "rx", "mail", "order", "retail", "outlet", "store", "market", "express", "central", "city",
             "metro", "metropolitan", "saint", "st", "mount", "upper", "lower", "greater", "new", "old", "united",
             "american", "international", "global", "premier", "advanced", "integrated", "comprehensive", "quality",
             "first", "choice", "life", "associated", "affiliated", "statewide", "area", "district", "region",
             "branch", "location", "site", "annex", "pavilion", "tower", "plaza"}
STREET_WORDS = {"st", "street", "ave", "avenue", "rd", "road", "ln", "lane", "dr", "drive", "blvd", "boulevard", "ct",
                "court", "way", "pl", "place", "apt", "apartment", "suite", "ste", "unit", "po", "box", "p.o.", "n",
                "s", "e", "w", "ne", "nw", "se", "sw", "north", "south", "east", "west", "hwy", "highway", "pkwy",
                "parkway", "cir", "circle", "ter", "terrace", "trl", "trail", "sq", "square", "floor", "fl", "bldg",
                "building", "rte", "route", "close", "crescent", "row", "green", "gardens", "mews", "flat"}

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
MON = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|"
       r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
YEAR = r"(?:19|20|21)\d\d"

# ------------------------------------------------------------------ patterns for text

DATES = [   # (group name, pattern): every form moves by the run's offset and keeps its own spelling
    ("D1", rf"(?<![\d.])(?P<y1>{YEAR})(?P<s1>[-/.])(?P<m1>0?[1-9]|1[0-2])(?P=s1)(?P<d1>0?[1-9]|[12]\d|3[01])(?!\d)"),
    ("D2", rf"(?<![\d.])(?P<y2>{YEAR})(?P<m2>0[1-9]|1[0-2])(?P<d2>0[1-9]|[12]\d|3[01])(?P<t2>(?:\d\d){{0,3}}(?:\.\d+)?)(?![\d.])"),
    ("D3", rf"(?<![\d/.:-])(?P<a3>\d{{1,2}})(?P<s3>[/.-])(?P<b3>\d{{1,2}})(?P=s3)(?P<y3>{YEAR}|(?<=/)\d\d)(?![\d/]|\.\d)"),
    ("D4", rf"(?<![A-Za-z])(?P<n4>(?i:{MON}))(?P<p4>\.?\s+)(?P<y4>{YEAR})(?!\d)"),
    ("D5", rf"(?<![A-Za-z])(?P<n5>(?i:{MON}))(?P<p5>\.?\s+)(?P<d5>[0-3]?\d)(?P<o5>(?i:st|nd|rd|th))?(?!\d)"
           rf"(?:(?P<c5>,?\s+)(?P<y5>{YEAR})(?!\d))?"),
    ("D6", rf"(?<![\d.])(?P<d6>[0-3]?\d)(?P<o6>(?i:st|nd|rd|th))?(?P<s6>[\s-])(?P<n6>(?i:{MON}))(?P<p6>\.?[\s,-]+)"
           rf"(?P<y6>{YEAR}|\d\d)(?!\d)"),
]
NAMEWORD = r"[A-Z][a-zA-Z'’]*[a-zA-Z](?:-[A-Z][a-zA-Z'’]*)?"
NAMES = rf"{NAMEWORD}(?:\s+(?:[A-Z]\.|{NAMEWORD})){{0,2}}"
CRED = (r"(?:,\s*(?:DO|D\.O\.|PA)|,?\s+(?:MD|M\.D\.|RN|NP|PA-C|PhD|DDS|DMD|MBBS|FNP(?:-BC)?|APRN|CNP|CNM|LPN|DPM|"
        r"PharmD|LCSW|DPT|CRNA|ARNP))(?![A-Za-z])")
LABELS = (r"(?i:patient(?:\s+name)?|name|member(?:\s+name)?|subscriber(?:\s+name)?|guarantor|guardian|spouse|"
          r"emergency\s+contact|contact|provider|physician|doctor|pcp|attending|ordered\s+by|ordering\s+provider|"
          r"referring\s+provider|(?:electronically\s+)?signed\s+by|author|prepared\s+for|export(?:ed)?(?:\s+for)?)")
ID_LABELS = (r"(?i:mrn|medical\s+record(?:\s+(?:number|no\.?|#))?|member(?:\s+(?:id|number|no\.?|#))?|"
             r"subscriber(?:\s+(?:id|number|#))?|account(?:\s+(?:number|no\.?|#))?|acct\.?(?:\s*#)?|"
             r"policy(?:\s+(?:number|no\.?|#))?|group\s+(?:number|no\.?|#)|claim(?:\s+(?:number|no\.?|#|id))?|"
             r"patient\s+id|id|ssn|npi|auth(?:orization)?(?:\s+(?:number|no\.?|#))?|rx(?:\s*(?:number|no\.?|#))?|"
             r"order\s+(?:number|no\.?|#)|accession(?:\s+(?:number|no\.?|#))?|case\s+(?:number|no\.?|#)|"
             r"ref(?:erence)?\s+(?:number|no\.?|#))")
_KINDS = ["Hospital", "Hospitals", "Clinic", "Clinics", "Medical Center", "Medical Centre", "Health", "Healthcare",
          "Laboratory", "Laboratories", "Lab", "Labs", "Pharmacy", "Imaging", "Radiology", "Center", "Centre",
          "Associates", "Partners", "Group", "Practice", "Infirmary", "Diagnostics", "Physicians", "Specialists",
          "Oncology", "Cardiology", "Hematology", "Haematology", "Dermatology", "Neurology", "Urology", "Orthopedics",
          "Pediatrics", "Surgery", "Surgical", "Therapy", "Rehabilitation", "Dental", "Hospice", "Pathology",
          "Dialysis"]
ORG_KINDS = "(?:" + "|".join(k.replace(" ", r"\s+") for k in sorted(_KINDS + [k.upper() for k in _KINDS], key=len,
                                                                     reverse=True)) + r")(?![A-Za-z])"
TEXT_RULES = [
    ("EM", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
    ("KID", None),   # known identifiers too short for the shapes below (pass 2 only)
    ("KPH", None),   # known addresses and other phrases (pass 2 only)
    ("SSN", r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"),
    ("PH", r"(?<![\w+])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d-])"
           r"|(?<![\w+])\+\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?![\d-])"),
] + DATES + [
    ("LID", rf"(?<![A-Za-z])(?P<lid_t>{ID_LABELS})(?P<lid_s>\s*[:#]?\s*)(?P<lid_v>(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{{3,}})"),
    ("HON", rf"(?<![A-Za-z])(?P<hon_t>(?:Dr|Mr|Mrs|Ms|Mx|Miss|Prof|Dear|DR|MR|MRS|MS|MISS|PROF|DEAR)\.?\s+)(?P<hon_n>{NAMES})"),
    ("CRD", rf"(?<![A-Za-z])(?P<crd_n>{NAMEWORD}(?:\s+(?:[A-Z]\.|{NAMEWORD})){{1,2}})(?P<crd_t>{CRED})"),
    ("LAB", rf"(?<![A-Za-z])(?P<lab_t>{LABELS}\s*:\s*)(?P<lab_n>{NAMEWORD}(?:,?\s+(?:[A-Z]\.|{NAMEWORD})){{0,3}})"),
    ("ORG", rf"(?<![A-Za-z])(?P<org_n>(?:{NAMEWORD}\s+){{1,3}})(?P<org_k>{ORG_KINDS})"),
    ("IDT", r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?![A-Za-z0-9_-])"),
    ("DIG", r"(?<![\d.,])\d{7,}(?![\d.,])"),
    ("KNM", r"(?<![A-Za-z])[A-Za-z][A-Za-z'’]*(?![A-Za-z])"),   # a word: replaced if it's a known name (pass 2)
]
DATE_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in DATES))
DATEISH = re.compile(rf"(?i)^\s*(?:\d|[-/.,:+T ]|Z|AM|PM|UTC|GMT|{MON}|(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*|st|nd|rd|th)+\s*$")
HL7_TS = re.compile(r"^(?P<y>(?:19|20|21)\d\d)(?P<m>0[1-9]|1[0-2])?(?P<d>0[1-9]|[12]\d|3[01])?(?P<rest>(?:\d\d){0,3}(?:\.\d+)?(?:[+-]\d{4})?)$")
UNIT_NUMBER = re.compile(r"^\d+(?:\.\d+)?[A-Za-z%/]{1,6}$")
NUMBER = re.compile(r"^(\s*[<>]=?\s*)?(-?)(\d+)(?:([.,])(\d+))?(\s*)$")
WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")
HTML_TEXT = re.compile(r"(^|>)([^<]+)(?=<|$)")

# ------------------------------------------------------------------ JSON keys

KEEP_KEYS = {"resourcetype", "system", "code", "unit", "units", "status", "use", "contenttype", "language", "gender",
             "sex", "currency", "comparator", "mode", "intent", "priority", "k", "kind", "type", "category", "cat",
             "ext", "flag", "abn", "interpretationcode", "version", "profile", "url", "resulttype", "claimtype",
             "claimstatus", "contacttype", "specimensdisplay", "loinc", "cpt", "hcpcs", "icd", "icd10", "snomed",
             "rxnorm", "ndc", "valueset", "codesystem", "codesystemname", "lang", "locale", "timezone", "tz"}
KEEP_SUFFIXES = ("code", "codes", "system", "unit", "units", "status", "type", "kind", "currency", "flag")
ID_KEYS = {"mrn", "ssn", "npi", "mbi", "hicn", "medicareid", "medicaidid", "memberid", "membernumber", "memberno",
           "subscriberid", "subscribernumber", "patientid", "personid", "accountnumber", "accountid", "account",
           "acct", "policynumber", "policyid", "groupnumber", "groupid", "claimid", "claimnumber", "claimno",
           "authorizationnumber", "authnumber", "authorizationid", "authorization", "prescriptionnumber", "rxnumber",
           "ordernumber", "orderid", "accessionnumber", "accession", "encounterid", "encounternumber", "visitid",
           "visitnumber", "caseid", "casenumber", "referralid", "identifier", "externalid", "sourceid", "documentid",
           "cardnumber", "licensenumber", "license", "passport", "driverslicense", "insuranceid", "employeeid",
           "userid", "username", "login", "deviceid", "serialnumber", "udi", "mrnnumber", "chartnumber", "taxid",
           "ein", "dea", "deanumber", "nationalid", "nhsnumber", "healthcardnumber", "insurancenumber"}
ID_SUFFIX = re.compile(r"(?:Id|ID|_id|-id|_ID|Number|_number|Num|_num|No|_no)$")
PHONE_KEY = re.compile(r"(?i)phone|telephone|^tel$|mobile|^cell|fax|pager")
EMAIL_KEY = re.compile(r"(?i)e-?mail")
BIRTH_KEY = re.compile(r"(?i)birth|^dob$|dateofbirth|birthdate|^born")
DATE_KEY = re.compile(r"(?i)date|time|instant|^dt$|when|period|start|end|issued|created|updated|recorded|"
                      r"written|authored|onset|abatement|collected|received|exported|built|^d$|^t$|admit|discharge")
STREET_KEY = re.compile(r"(?i)^(?:address|addr|street|streetaddress|address[_ ]?line\d?|line\d?|address\d)$")
CITY_KEY = re.compile(r"(?i)^(?:city|town|district|county|municipality|locality|suburb)$")
POSTAL_KEY = re.compile(r"(?i)^(?:zip|zipcode|zip_code|postal|postalcode|postal_code|postcode)$")
PERSON_NAME_KEYS = {"firstname", "lastname", "middlename", "givenname", "familyname", "surname", "maidenname",
                    "mothersmaidenname", "nickname", "preferredname", "legalname"}
EITHER_KEYS = {"fullname", "patientname", "membername", "subscribername", "providername", "orderprovidername",
               "authorname", "sendername", "recipientname", "doctorname", "physicianname", "prescribername",
               "clinicianname", "nursename", "guarantorname", "who", "doctor", "physician", "provider", "prescriber",
               "orderingprovider", "referringprovider", "attendingprovider", "renderingprovider", "author", "sender",
               "recipient", "pcp", "signedby", "orderedby", "performedby", "enteredby", "prov", "attending",
               "authorizedby", "resultedby", "reviewedby", "contactname", "emergencycontact", "nextofkin", "patient"}
ORG_KEYS = {"where", "location", "facility", "department", "clinic", "hospital", "practice", "pharmacy",
            "laboratory", "insurer", "ins", "payer", "payor", "organization", "organisation", "org", "custodian",
            "site", "facilityname", "locationname", "departmentname", "clinicname", "hospitalname", "practicename",
            "pharmacyname", "laboratoryname", "insurername", "payername", "payorname", "organizationname",
            "organisationname", "orgname", "sitename", "employer", "employername", "vendor", "company",
            "performinglab", "resultinglab", "performinglabname"}
PERSON_HINT = re.compile(r"(?i)^(?:users?|authors?|senders?|recipients?|from|to|providers?|practitioners?|"
                         r"performers?|requester|recorder|asserter|prescriber|participants?|individual|attender|"
                         r"doctors?|physicians?|clinicians?|careteam|patient|subject|beneficiary|subscriber|"
                         r"policyholder|guarantor|contacts?|emergencycontact|guardian|relatedperson|employees?|staff|"
                         r"nurses?|orderingprovider|renderingprovider|referringprovider|attendingprovider|pcp|signer|"
                         r"verifier|enterer|informant|members?|people|persons?|caregivers?|generalpractitioner)$")
ORG_HINT = re.compile(r"(?i)^(?:departments?|organi[sz]ations?|orgs?|facility|facilities|locations?|sites?|"
                      r"resultinglab|performinglab|laborator(?:y|ies)|clinics?|hospitals?|practices?|pharmac(?:y|ies)|"
                      r"payors?|payers?|insurers?|insurance|custodian|serviceprovider|managingorganization|assigner|"
                      r"vendors?|employer|servicelocation)$")
PERSON_RT = {"Patient", "Practitioner", "RelatedPerson", "Person"}
ORG_RT = {"Organization", "Location", "HealthcareService", "InsurancePlan", "Endpoint", "OrganizationAffiliation"}
NOTE_KEYS = {"body", "bodyhtml", "bodytext", "html", "note", "notes", "comment", "comments", "remark", "remarks",
             "narrative", "impression", "conclusion", "contentasstring", "contentashtml", "findings", "history",
             "message", "messagebody", "messagetext", "letter", "lettertext", "subject", "div", "notetext",
             "clinicalnotes", "progressnote", "freetext", "patientinstruction", "summarytext", "addendum"}
NOTE_CTX = {"note", "notes", "comment", "comments", "providercomments", "addenda", "addendum", "annotation",
            "annotations", "narrative", "impression", "findings", "messages", "remarks"}
VALUE_KEYS = {"value", "numericvalue", "resultvalue", "result", "valuenumeric", "measurement", "reading"}
NOT_VALUE_CTX = re.compile(r"(?i)range|amount|balance|billed|paid|pays|charge|cost|price|responsibility|allowed|"
                           r"deductible|copay|coinsurance|adjudication|total|benefit|owe|liability|limit|low|high")

# ------------------------------------------------------------------ CSV headers

CSV_BIRTH = re.compile(r"(?i)\b(?:dob|birth|birthdate|geburt)")
CSV_DATE = re.compile(r"(?i)date|datum|fecha|time|when|collected|drawn|reported|resulted|period|service|\bdos\b|"
                      r"admit|discharge|visit|day|issued|received|from|through|thru")
CSV_PHONE = re.compile(r"(?i)phone|\btel\b|mobile|\bcell\b|fax")
CSV_EMAIL = re.compile(r"(?i)e-?mail")
CSV_STREET = re.compile(r"(?i)address|street|\baddr\b")
CSV_CITY = re.compile(r"(?i)\bcity\b|\btown\b|county")
CSV_POSTAL = re.compile(r"(?i)\bzip|postal|postcode")
CSV_ID = re.compile(r"(?i)\bmrn\b|\bssn\b|\bnpi\b|\bid\b|identifier|number|\bno\b\.?|#|account|\bacct\b|^member$|"
                    r"policy|accession|prescription|\brx\b")
CSV_NOT_PERSON = re.compile(r"(?i)test|lab|analyte|component|drug|medication|panel|procedure|service|product|item|"
                            r"code|description|order(?!ed)")
CSV_PERSON = re.compile(r"(?i)patient|provider|doctor|physician|prescriber|ordered by|ordering|clinician|author|"
                        r"attending|referring|nurse|member name|subscriber|guarantor|first name|last name|full name|"
                        r"^name$|staff|signed|performed by|rendering")
CSV_ORG = re.compile(r"(?i)facility|location|clinic|hospital|pharmacy|laboratory|^lab$|performing lab|department|"
                     r"practice|\bsite\b|insurer|payer|payor|plan name|organi[sz]ation")
CSV_VALUE = re.compile(r"(?i)^\s*(?:value|result|results|wert|ergebnis|resultat|valeur|reading|measurement|"
                       r"numeric value|result value|valor)\s*$")
CSV_NOTE = re.compile(r"(?i)comment|note|remark|narrative|message|\btext\b")

# ------------------------------------------------------------------ files

XML_EXT = {".xml", ".cda", ".ccd", ".ccda", ".xsl", ".xslt"}
JSON_EXT = {".json", ".fhir"}
NDJSON_EXT = {".ndjson", ".jsonl"}
CSV_EXT = {".csv", ".tsv", ".tab", ".psv"}
TEXT_EXT = {".txt", ".text", ".md", ".html", ".htm", ".xhtml", ".ics", ".vcf", ".log", ".rst"}
PDF_EXT = {".pdf"}
IMAGE_EXT = {".png": "png", ".gif": "gif", ".jpg": "jpeg", ".jpeg": "jpeg", ".jpe": "jpeg"}
OTHER_DOC_EXT = {".tif", ".tiff", ".heic", ".heif", ".bmp", ".webp", ".doc", ".docx", ".odt", ".rtf", ".xls",
                 ".xlsx", ".ods", ".ppt", ".pptx", ".dcm", ".dicom", ".mp4", ".mov", ".m4a", ".mp3", ".wav", ".avi",
                 ".svg", ".eml", ".msg"}
LOCATION_EXT = {".gpx", ".kml", ".kmz", ".geojson"}
ARCHIVE_EXT = {".tar", ".gz", ".tgz", ".7z", ".rar", ".bz2", ".xz", ".zst"}
RECORDS = {"xml", "json", "ndjson", "csv", "text"}   # kinds that hold records the tool can clean


def _pdf(text=PLACEHOLDER_TEXT):
    """A one-page PDF that says the document was removed."""
    content = b"BT /F1 14 Tf 72 720 Td (" + text.encode("ascii") + b") Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def _png():
    """A 1 x 1 grey PNG."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xc8")) + chunk(b"IEND", b""))


def _jpeg():
    """An 8 x 8 grey baseline JPEG: one quantisation table, one Huffman code each for DC 0 and end-of-block."""
    seg = lambda marker, body: b"\xff" + marker + struct.pack(">H", len(body) + 2) + body
    return (b"\xff\xd8"
            + seg(b"\xdb", b"\x00" + b"\x01" * 64)
            + seg(b"\xc0", b"\x08\x00\x08\x00\x08\x01\x01\x11\x00")
            + seg(b"\xc4", b"\x00\x01" + b"\x00" * 15 + b"\x00")
            + seg(b"\xc4", b"\x10\x01" + b"\x00" * 15 + b"\x00")
            + seg(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
            + b"\x3f\xff\xd9")


GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00"
       b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
PLACEHOLDERS = {"pdf": _pdf, "png": _png, "jpeg": _jpeg, "gif": lambda: GIF}


def media_kind(content_type):
    """"pdf", "png", "jpeg", "gif", "text" or None for a MIME type."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == "application/pdf":
        return "pdf"
    if ct in ("image/png", "image/jpeg", "image/jpg", "image/gif"):
        return {"image/png": "png", "image/gif": "gif"}.get(ct, "jpeg")
    if ct.startswith("text/") or ct in ("application/xml", "application/json", "application/xhtml+xml",
                                       "application/fhir+json", "application/fhir+xml", "application/hl7-v3+xml"):
        return "text"
    return None


# ------------------------------------------------------------------ paths and zips

_DRIVE = re.compile(r"^[A-Za-z]:")


def clean_path(path):
    """A relative path inside the export, normalised, or Refused: no absolute paths, "..", drive letters,
    backslashes or control characters (as server/uploads.py's clean_path)."""
    if not isinstance(path, str) or not path.strip():
        raise Refused("A file in the export has no name.")
    if "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise Refused(f"Not allowed in a file name: a backslash or a control character ({path[:200]!r}).")
    path = unicodedata.normalize("NFC", path)
    if path.startswith("/") or _DRIVE.match(path):
        raise Refused(f"The export holds an absolute path ({path[:200]!r}), which isn't allowed.")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts:
        raise Refused("A file in the export has no name.")
    if ".." in parts:
        raise Refused(f"A path in the export goes up a folder with .. ({path[:200]!r}), which isn't allowed.")
    if len(path) > 1024 or len(parts) > 32 or any(len(p.encode("utf-8")) > 255 for p in parts):
        raise Refused(f"A path in the export is too long ({path[:200]!r}).")
    return "/".join(parts)


class Budget:
    def __init__(self):
        self.files, self.bytes = MAX_FILES, MAX_TOTAL

    def take(self, n_files=0, n_bytes=0):
        self.files -= n_files
        self.bytes -= n_bytes
        if self.files < 0:
            raise Refused(f"The export holds more than {MAX_FILES} files, more than this tool reads.")
        if self.bytes < 0:
            raise Refused(f"The export is more than {MAX_TOTAL // GB} GB unpacked, more than this tool reads.")


def unzip(zpath, dest, budget, name):
    """Unpack the zip at zpath into dest, refusing symbolic links, password-protected members, unsafe paths,
    clashing names and members that unpack to far more than their size. Returns {rel: abs path}."""
    out, seen, folders = {}, set(), set()
    try:
        zsize = os.path.getsize(zpath)
        with zipfile.ZipFile(zpath) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith(MACOS_FORKS)]
            budget.take(n_files=len(infos))
            for i in infos:
                if stat.S_ISLNK(i.external_attr >> 16):
                    raise Refused(f"{name} holds a symbolic link ({i.filename[:200]}), which isn't allowed.")
                if i.flag_bits & 0x1:
                    raise Refused(f"{name} is password-protected. Unzip it yourself, then run this on the folder.")
                try:
                    rel = clean_path(i.filename)
                except Refused as e:
                    raise Refused(f"{name}: {e}") from None
                cf = rel.casefold()
                parents = {cf.rsplit("/", i)[0] for i in range(1, cf.count("/") + 1)}
                if cf in seen or cf in folders or parents & seen:
                    raise Refused(f"{name}: {rel} clashes with another file in it (the same name, or a file named "
                                  "like a folder).")
                seen.add(cf)
                folders |= parents
                path = os.path.join(dest, *rel.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                denom, n = max(min(i.compress_size, zsize), 1), 0
                with zf.open(i) as src, open(path, "xb") as dst:
                    while piece := src.read(MB):
                        n += len(piece)
                        if n > MAX_FILE:
                            raise Refused(f"{name}: {rel} is more than {MAX_FILE // GB} GB unpacked.")
                        if n > RATIO_FROM and n > MAX_RATIO * denom:
                            raise Refused(f"{name} unpacks to far more than its size (at {rel}): it looks like a zip "
                                          "bomb, so it was refused.")
                        budget.take(n_bytes=len(piece))
                        dst.write(piece)
                out[rel] = path
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, ValueError, OSError) as e:
        raise Refused(f"{name} can't be unpacked ({e}).") from None
    return out


def walk(root, budget, report):
    """{rel: abs path} for a folder, not following symbolic links (they are left out, and said so)."""
    out = {}
    for d, dirs, names in os.walk(root):
        for x in list(dirs):
            if os.path.islink(os.path.join(d, x)):
                report.leave_out(os.path.relpath(os.path.join(d, x), root).replace(os.sep, "/"), "a symbolic link")
                dirs.remove(x)
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if os.path.islink(p) or not os.path.isfile(p):
                report.leave_out(rel, "a symbolic link or special file")
                continue
            rel = clean_path(rel)
            budget.take(n_files=1, n_bytes=os.path.getsize(p))
            out[rel] = p
    return out


# ------------------------------------------------------------------ the report

KIND_ORDER = ["people's names", "organisation names", "identifiers", "phone numbers", "email addresses", "addresses",
              "dates", "birth dates", "notes replaced", "documents replaced", "values jittered", "file names"]


class Report:
    def __init__(self):
        self.changed = collections.Counter()
        self.left_out = []         # (rel, reason)
        self.unknown = []          # rel: read as a format the tool doesn't know, cleaned by the general rules
        self.kept_text = []        # (rel, where): free text kept after cleaning
        self.unread_dates = collections.Counter()   # rel -> dates it couldn't read
        self.notes = []            # other things to say
        self.files = 0
        self.offset_days = 0

    def leave_out(self, rel, reason):
        self.left_out.append((rel, reason))

    def as_dict(self):
        return {"changed": dict(self.changed), "left_out": list(self.left_out), "unknown_format": list(self.unknown),
                "kept_text": list(self.kept_text), "unreadable_dates": dict(self.unread_dates),
                "notes": list(self.notes), "files": self.files, "offset_days": self.offset_days}

    def text(self, out_path):
        lines = [f"Anonymised copy written to {out_path} ({self.files} file{'s' if self.files != 1 else ''})."]
        if self.changed:
            lines.append("Changed:")
            for k in KIND_ORDER + sorted(set(self.changed) - set(KIND_ORDER)):
                if self.changed.get(k):
                    lines.append(f"  {k}: {self.changed[k]}")
            if self.changed.get("dates") or self.changed.get("birth dates"):
                lines.append("  (every date moved back by the same number of whole weeks: the order, the gaps between "
                             "dates and the weekdays are as they were; birth dates are 1 January of their year)")
        if self.left_out:
            lines.append(f"Left out of the copy ({len(self.left_out)}):")
            lines += [f"  {rel}: {why}" for rel, why in self.left_out[:30]]
            if len(self.left_out) > 30:
                lines.append(f"  ... and {len(self.left_out) - 30} more")
        check = []
        if self.unknown:
            check.append(f"{len(self.unknown)} file(s) in a format this tool doesn't know were cleaned by its general "
                         "rules only: " + ", ".join(self.unknown[:5]) + (" ..." if len(self.unknown) > 5 else ""))
        if self.kept_text:
            files = collections.Counter(r for r, _ in self.kept_text)
            check.append(f"{len(self.kept_text)} piece(s) of free text were kept, cleaned of the names, numbers and "
                         "dates the tool recognised. Read them: names it didn't recognise are still there. For example:")
            for rel, where in self.kept_text[:12]:
                check.append(f"    {rel}: {where}")
            if len(files) > 1 or len(self.kept_text) > 12:
                check.append("    (" + ", ".join(f"{r}: {n}" for r, n in files.most_common(8)) + ")")
        if self.unread_dates:
            check.append("Dates it couldn't read were left as they are: " +
                         ", ".join(f"{r} ({n})" for r, n in self.unread_dates.most_common(8)))
        check += self.notes
        if check:
            lines.append("Look these over before you share the copy:")
            lines += [f"  - {c}" if not c.startswith("    ") else c for c in check]
        lines.append("Automated cleaning misses things. Open the copy and look through it before you share it: search "
                     "it for your surname, your date of birth, your street and your record numbers "
                     "(docs/anonymising-exports.md).")
        return "\n".join(lines)


# ------------------------------------------------------------------ the anonymiser

def _case_like(orig, fake):
    if orig.isupper() and len(orig) > 1:
        return fake.upper()
    if orig.islower():
        return fake.lower()
    if orig[:1].isupper():
        return fake[:1].upper() + fake[1:]
    return fake


def _norm(word):
    return word.replace("’", "'").casefold()


def _words(s):
    return len(WORD.findall(s))


class Anonymiser:
    def __init__(self, seed=None, jitter=0.0, notes="replace", keep_orgs=False):
        self.key = hashlib.sha256(b"sanguity-anonymise\0" + (seed.encode("utf-8") if seed is not None
                                                             else secrets.token_bytes(32))).digest()
        self.jitter = max(0.0, float(jitter or 0)) / 100.0
        self.notes = notes
        self.keep_orgs = keep_orgs
        self.offset = timedelta(days=-7 * self.rng("offset").randint(26, 156))
        self.report = Report()
        self.report.offset_days = self.offset.days
        self.collecting = True
        self.person_tokens = {}    # normalised word -> "given" | "family"
        self.org_tokens = set()
        self.place_tokens = set()  # words of addresses and cities
        self.fakes = {}            # normalised word -> made-up word
        self.fakes_used = set()
        self.ids, self.used_ids = {}, set()
        self.phones, self.emails = {}, {}
        self.phrases = {}          # casefolded address line or city -> made-up one
        self.births = set()        # (y, m, d) of birth dates
        self.dayfirst_votes = [0, 0]
        self.dayfirst = False
        self.rel = ""
        self.cache = {}
        self.text_rx = self._compile(known=False)
        self.date_rx = DATE_RE

    # -------------------------------------------------------------- randomness: the same input and seed, the same copy
    def rng(self, kind, value=""):
        return random.Random(hmac.new(self.key, f"{kind}\0{value}".encode("utf-8"), hashlib.sha256).digest())

    def count(self, kind, n=1):
        if not self.collecting:
            self.report.changed[kind] += n

    # -------------------------------------------------------------- between the passes
    def finish_collecting(self):
        """Pass 1 has seen every file: settle the slash-date order and build the pattern of known names."""
        self.collecting = False
        self.dayfirst = self.dayfirst_votes[0] > self.dayfirst_votes[1]
        self.originals = set(self.person_tokens) | self.org_tokens | self.place_tokens
        self.text_rx = self._compile(known=True)
        self.cache = {}

    def _compile(self, known):
        parts = []
        for name, pat in TEXT_RULES:
            if name == "KID":   # the identifiers the shapes below (IDT, DIG) don't catch: few digits, or 4-6 digits only
                short = sorted((i for i in self.ids if len(i) >= 4 and not re.match(r"\d+\.", i) and (
                    sum(c.isdigit() for c in i) < 4 or (i.isdigit() and len(i) < 7))), key=len, reverse=True)[:3000] \
                    if known else []
                if not short:
                    continue
                pat = r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, short)) + r")(?![A-Za-z0-9])"
            elif name == "KPH":
                ph = sorted((p for p in self.phrases if len(p) >= 4), key=len, reverse=True)[:3000] if known else []
                if not ph:
                    continue
                pat = r"(?<![A-Za-z0-9])(?i:" + "|".join(map(re.escape, ph)) + r")(?![A-Za-z0-9])"
            elif name == "KNM" and not known:
                continue
            parts.append(f"(?P<{name}>{pat})")
        return re.compile("|".join(parts))

    # -------------------------------------------------------------- made-up words
    def _fake_word(self, w, role):
        """The made-up word for a name word (normalised), the same every time in a run."""
        if w in self.fakes:
            return self.fakes[w]
        r = self.rng(role, w)
        for _ in range(50):
            if role == "given":
                f = r.choice(GIVEN)
            elif role == "family":
                f = r.choice(FAMILY_A) + r.choice(FAMILY_B)
            else:
                f = r.choice(PLACE_A) + r.choice(PLACE_B)
            if f.casefold() != w and f.casefold() not in getattr(self, "originals", ()) and f not in self.fakes_used:
                break
        self.fakes[w] = f
        self.fakes_used.add(f)
        return f

    def _letter(self, c):
        f = self._fake_word("initial:" + c.casefold(), "given")[0]
        return f.upper() if c.isupper() else f.lower()

    # -------------------------------------------------------------- people and organisations
    def _name_words(self, s, role=None):
        """[(start, end, word, role)] for the words of a person's name that are to be replaced."""
        words = list(re.finditer(r"[A-Za-z][A-Za-z'’]*", s))
        comma = s.find(",")
        if comma > 0:
            after = WORD.findall(s[comma + 1:])
            if after and after[0].casefold() in CREDENTIALS:
                comma = -1
        named = [m for m in words if m.group().casefold() not in PREFIXES and m.group().casefold() not in CREDENTIALS
                 and _norm(m.group()) not in STOP and not m.group().islower()]
        out = []
        for i, m in enumerate(named):
            if role:
                r = role
            elif comma > 0:
                r = "family" if m.start() < comma else "given"
            else:
                r = "family" if i == len(named) - 1 else "given"
            out.append((m.start(), m.end(), m.group(), r))
        return out

    def person(self, s, count=True, role=None):
        if not isinstance(s, str) or not s.strip():
            return s
        parts = self._name_words(s, role)
        if self.collecting:
            for _, _, w, r in parts:
                if len(w) > 1:
                    self.person_tokens.setdefault(_norm(w), r)
            return s
        if not parts:
            return s
        out, at = [], 0
        for a, b, w, r in parts:
            out.append(s[at:a])
            out.append(self._letter(w) if len(w) == 1 else _case_like(w, self._fake_word(_norm(w), r)))
            at = b
        out.append(s[at:])
        if count:
            self.count("people's names")
        return "".join(out)

    def org(self, s, count=True):
        if not isinstance(s, str) or not s.strip():
            return s
        if self.keep_orgs:
            return self.text(s, flag=False)
        words = [m for m in re.finditer(r"[A-Za-z][A-Za-z'’]*", s)
                 if _norm(m.group()) not in ORG_WORDS and _norm(m.group()) not in STOP and len(m.group()) > 2
                 and not m.group().islower()]
        if self.collecting:
            for m in words:
                self.org_tokens.add(_norm(m.group()))
            self.text(s, flag=False)   # a person's name inside ("Dr. Lin's practice")
            return s
        out, at = [], 0
        for m in words:
            out.append(s[at:m.start()])
            out.append(_case_like(m.group(), self._fake_word(_norm(m.group()), "place")))
            at = m.end()
        out.append(s[at:])
        if words and count:
            self.count("organisation names")
        return self.text("".join(out), flag=False, names=False)

    @staticmethod
    def looks_like_org(s):
        words = WORD.findall(s or "")
        if not words:
            return False
        if any(_norm(w) in ORG_WORDS - {"the", "of", "and", "at", "for", "main", "north", "south", "east", "west"}
               for w in words):
            return True
        return bool(re.search(r"\d|#|&|@", s)) or len(words) > 4

    def either(self, s):
        if not isinstance(s, str):
            return s
        return self.org(s) if self.looks_like_org(s) else self.person(s)

    def device(self, s):
        """An Apple Health source or device name: "Robin's Apple Watch" loses the owner's name."""
        if not s:
            return s
        key = ("device", s)
        if key in self.cache:
            return self.cache[key]
        out = re.sub(r"(?<![A-Za-z])([A-Z][A-Za-z-]*)(['’]s)(?=\s)", lambda m: self.person(m.group(1)) + m.group(2), s)
        out = re.sub(r"(name:)([^,>]*)", lambda m: m.group(1) + self.text(m.group(2), flag=False), out)
        if len(self.cache) < 100000:
            self.cache[key] = out
        return out

    # -------------------------------------------------------------- identifiers, phones, emails, addresses
    def ident(self, s, kind="id"):
        """A made-up identifier of the same shape, the same for the same original everywhere in the run. Codes too
        short to identify anyone (under four letters and digits) are kept, so keys like "e1" still match up."""
        if isinstance(s, (int, float)) and not isinstance(s, bool):
            if isinstance(s, float) and not s.is_integer():
                return s
            new = self.ident(str(int(s)), kind)
            return int(new) if new.isdigit() else new
        if not isinstance(s, str):
            return s
        core = s.strip()
        if sum(c.isalnum() for c in core) < 4:
            return s
        if self.collecting:
            self.ids.setdefault(core, None)
            return s
        fake = self.ids.get(core)
        if fake is None:
            r = self.rng("id", core)
            for attempt in range(100):
                fake = self._npi(r) if kind == "npi" and re.fullmatch(r"\d{10}", core) else self._shape(core, r, attempt)
                if fake != core and fake not in self.used_ids and fake not in self.ids:
                    break
            self.ids[core] = fake
            self.used_ids.add(fake)
        self.count("identifiers")
        return s.replace(core, fake)

    @staticmethod
    def _shape(s, r, attempt):
        if any(c.isdigit() for c in s) and (re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", s, re.I)
                                             or re.fullmatch(r"[0-9a-f]{8,}", s) and sum(c.isalpha() for c in s) >= 2
                                             or re.fullmatch(r"[0-9A-Fa-f]{16}|[0-9A-Fa-f]{24}|[0-9A-Fa-f]{32,}", s)):
            upper = s == s.upper() and any(c.isalpha() for c in s)
            return "".join(c if c == "-" else (r.choice("0123456789ABCDEF") if upper else r.choice("0123456789abcdef"))
                           for c in s)
        runs = re.findall(r"[A-Za-z]+|\d+|[^A-Za-z\d]+", s)
        out = []
        for i, run in enumerate(runs):
            if run.isdigit():
                d = [r.choice("0123456789") for _ in run]
                if run[0] != "0" and d[0] == "0":
                    d[0] = r.choice("123456789")
                out.append("".join(d))
            elif run.isalpha():
                if i == 0 and len(run) <= 4 and len(runs) > 1 and (run.isupper() or run.islower()) and attempt < 50:
                    out.append(run)   # a prefix such as "MRN", "PX" or "BC" says what kind of number it is
                else:
                    out.append("".join(r.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") if c.isupper()
                                       else r.choice("abcdefghjkmnpqrstuvwxyz") for c in run))
            else:
                out.append(run)
        return "".join(out)

    @staticmethod
    def _npi(r):
        """A made-up NPI with a valid check digit (Luhn over 80840 and the first nine digits)."""
        body = r.choice("12") + "".join(r.choice("0123456789") for _ in range(8))
        digits = [int(c) for c in "80840" + body]
        total = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2 == 0:
                d *= 2
                d = d - 9 if d > 9 else d
            total += d
        return body + str((10 - total % 10) % 10)

    def phone(self, s):
        if not isinstance(s, str) or not any(c.isdigit() for c in s):
            return self.ident(s) if isinstance(s, (int, float)) else s
        scheme = re.match(r"(?i)^(tel:|fax:|sms:)", s)
        pre, body = (scheme.group(1), s[scheme.end():]) if scheme else ("", s)
        digits = re.sub(r"\D", "", body)
        if len(digits) < 4 or self.collecting:
            return s
        if digits not in self.phones:
            r = self.rng("phone", digits)
            self.phones[digits] = "".join(r.choice("0123456789") for _ in digits)
        i = iter(self.phones[digits])
        self.count("phone numbers")
        return pre + re.sub(r"\d", lambda m: next(i), body)

    def email(self, s):
        if not isinstance(s, str) or "@" not in s or self.collecting:
            return s
        scheme = re.match(r"(?i)^mailto:", s)
        pre, body = (s[:scheme.end()], s[scheme.end():]) if scheme else ("", s)
        k = body.strip().casefold()
        if k not in self.emails:
            r = self.rng("email", k)
            self.emails[k] = f"{r.choice(GIVEN).lower()}.{r.choice(FAMILY_A).lower()}{r.choice(FAMILY_B)}@example.org"
        self.count("email addresses")
        return pre + self.emails[k]

    def contact(self, s):
        """An XML telecom value or a FHIR ContactPoint value: "tel:...", "mailto:..." or a bare number."""
        if not isinstance(s, str):
            return s
        if "@" in s:
            return self.email(s)
        if re.match(r"(?i)^https?:", s):
            return s
        return self.phone(s)

    def street(self, s):
        if not isinstance(s, str) or not s.strip():
            return s
        if self.collecting:
            self.phrases.setdefault(s.strip().casefold(), None)
            for m in WORD.finditer(s):
                if _norm(m.group()) not in STREET_WORDS and len(m.group()) > 2:
                    self.place_tokens.add(_norm(m.group()))
            return s
        k = s.strip().casefold()
        r = self.rng("street", k)

        def word(m):
            w = m.group()
            if w.isdigit():
                return "".join(r.choice("123456789" if i == 0 else "0123456789") for i in range(len(w)))
            if _norm(w) in STREET_WORDS or len(w) <= 2:
                return w
            return _case_like(w, self._fake_word(_norm(w), "place"))
        out = re.sub(r"\d+|[A-Za-z][A-Za-z'’.]*", word, s)
        self.phrases[k] = out.strip()
        self.count("addresses")
        return out

    def city(self, s):
        if not isinstance(s, str) or not s.strip():
            return s
        if self.collecting:
            self.phrases.setdefault(s.strip().casefold(), None)
            for m in WORD.finditer(s):
                if len(m.group()) > 2:
                    self.place_tokens.add(_norm(m.group()))
            return s
        out = re.sub(r"[A-Za-z][A-Za-z'’]*", lambda m: m.group() if len(m.group()) <= 2 else
                     _case_like(m.group(), self._fake_word(_norm(m.group()), "place")), s)
        self.phrases[s.strip().casefold()] = out.strip()
        self.count("addresses")
        return out

    def postal(self, s):
        if isinstance(s, (int, float)):
            return self.ident(s)
        if not isinstance(s, str) or not s.strip() or self.collecting:
            return s
        r = self.rng("postal", s.strip().casefold())
        self.count("addresses")
        return "".join(r.choice("0123456789") if c.isdigit() else (r.choice("ABCDEFGHJKLMNPRSTUVWXY") if c.isalpha()
                       else c) for c in s)

    def address_text(self, s):
        """A whole address in one string ("12 Elm St, Springfield, NE 68104")."""
        if not isinstance(s, str) or not s.strip():
            return s
        parts = s.split(",")
        if len(parts) == 1:
            return self.street(s)
        out = [self.street(parts[0])]
        postcode = r"\d{5}(?:-\d{4})?|\d{4}|[A-Z]\d[A-Z] ?\d[A-Z]\d|[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}"
        for p in parts[1:]:
            # " Springfield", " OR 97477", " Springfield OR 97477", " 97477", " USA"
            m = re.match(rf"^(\s*)(?:(.*?[A-Za-z])(\s+))??([A-Z]{{2}})?(\s*)({postcode})?(\s*)$", p)
            if m and (m.group(4) or m.group(6)) and (m.group(2) is None or m.group(4) or m.group(6)):
                out.append(m.group(1) + (self.city(m.group(2)) + m.group(3) if m.group(2) else "") + (m.group(4) or "")
                           + m.group(5) + (self.postal(m.group(6)) if m.group(6) else "") + m.group(7))
            elif re.fullmatch(r"\s*(?:USA?|United States|UK|United Kingdom|Canada)\s*", p):
                out.append(p)
            else:
                out.append(self.city(p))
        return ",".join(out)

    # -------------------------------------------------------------- dates
    def _move(self, y, m, d):
        """(y, m, d) moved by the offset, or a birth date generalised; None if it isn't a real date."""
        try:
            day = date(y, m, d)
        except ValueError:
            return None
        if (y, m, d) in self.births:
            self.count("birth dates")
            return ((day + self.offset).year, 1, 1)
        self.count("dates")
        new = day + self.offset
        return new.year, new.month, new.day

    @staticmethod
    def _pad(orig, n):
        return str(n).zfill(len(orig)) if len(orig) > 1 else str(n)

    @staticmethod
    def _month(orig, n):
        name = MONTHS[n - 1]
        o = orig.rstrip(".")
        full = o.casefold() in MONTHS
        out = name if full else ("sept" if o.casefold() == "sept" and n == 9 else name[:3])
        return _case_like(o, out.capitalize()) if not o.isupper() else out.upper()

    @staticmethod
    def _ordinal(orig, n):
        if not orig:
            return ""
        suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return suf.upper() if orig.isupper() else suf

    @staticmethod
    def _month_no(name):
        n = name.rstrip(".").casefold()[:3]
        return [m[:3] for m in MONTHS].index(n) + 1

    def _date_match(self, m, dayfirst=None):
        """The replacement for one date found by DATES; the original when it isn't a real date."""
        g = m.group
        if g("D1") is not None:
            new = self._move(int(g("y1")), int(g("m1")), int(g("d1")))
            if new is None:
                return self._unread(m)
            return f"{new[0]:04d}{g('s1')}{self._pad(g('m1'), new[1])}{g('s1')}{self._pad(g('d1'), new[2])}"
        if g("D2") is not None:
            new = self._move(int(g("y2")), int(g("m2")), int(g("d2")))
            if new is None:
                return self._unread(m)
            return f"{new[0]:04d}{new[1]:02d}{new[2]:02d}{g('t2')}"
        if g("D3") is not None:
            a, b, y = g("a3"), g("b3"), g("y3")
            if self.collecting:
                if int(a) > 12:
                    self.dayfirst_votes[0] += 1
                elif int(b) > 12:
                    self.dayfirst_votes[1] += 1
                return m.group()
            df = g("s3") == "." or int(a) > 12 or (int(b) <= 12 and (self.dayfirst if dayfirst is None else dayfirst))
            yy = int(y) if len(y) == 4 else (2000 + int(y) if int(y) < 50 else 1900 + int(y))
            d, mo = (a, b) if df else (b, a)
            new = self._move(yy, int(mo), int(d))
            if new is None:
                return self._unread(m)
            ny = f"{new[0]:04d}" if len(y) == 4 else f"{new[0] % 100:02d}"
            nd, nm = self._pad(d, new[2]), self._pad(mo, new[1])
            return f"{nd}{g('s3')}{nm}{g('s3')}{ny}" if df else f"{nm}{g('s3')}{nd}{g('s3')}{ny}"
        if g("D4") is not None:
            mid = self._move(int(g("y4")), self._month_no(g("n4")), 15)
            if mid is None:
                return m.group()
            return f"{self._month(g('n4'), mid[1])}{g('p4')}{mid[0]:04d}"
        if g("D5") is not None:
            mo, d = self._month_no(g("n5")), int(g("d5"))
            if g("y5") is None:
                if g("n5").islower() or self.collecting:   # "may 5" is probably not a date
                    return m.group()
                try:
                    new = date(2000 if (mo, d) == (2, 29) else 2001, mo, d) + self.offset
                except ValueError:
                    return self._unread(m)
                self.count("dates")
                return f"{self._month(g('n5'), new.month)}{g('p5')}{self._pad(g('d5'), new.day)}{self._ordinal(g('o5'), new.day)}"
            new = self._move(int(g("y5")), mo, d)
            if new is None:
                return self._unread(m)
            return (f"{self._month(g('n5'), new[1])}{g('p5')}{self._pad(g('d5'), new[2])}{self._ordinal(g('o5'), new[2])}"
                    f"{g('c5')}{new[0]:04d}")
        if g("D6") is not None:
            y = g("y6")
            yy = int(y) if len(y) == 4 else (2000 + int(y) if int(y) < 50 else 1900 + int(y))
            new = self._move(yy, self._month_no(g("n6")), int(g("d6")))
            if new is None:
                return self._unread(m)
            ny = f"{new[0]:04d}" if len(y) == 4 else f"{new[0] % 100:02d}"
            return (f"{self._pad(g('d6'), new[2])}{self._ordinal(g('o6'), new[2])}{g('s6')}{self._month(g('n6'), new[1])}"
                    f"{g('p6')}{ny}")
        return m.group()

    def _unread(self, m):
        if not self.collecting:
            self.report.unread_dates[self.rel] += 1
        return m.group()

    def dates(self, s, dayfirst=None):
        """s with every date in it moved (and nothing else changed)."""
        if self.collecting:
            self.date_rx.sub(lambda m: self._date_match(m), s)
            return s
        return self.date_rx.sub(lambda m: self._date_match(m, dayfirst), s)

    def date_value(self, s, dayfirst=None, compact=True):
        """A field that holds only a date (and a time): the moved date, or None when it isn't one."""
        if not isinstance(s, str) or not DATEISH.match(s) or not any(c.isdigit() for c in s):
            return None
        if not compact and re.fullmatch(r"\s*\d{8,}\s*", s):
            return None
        if not self.date_rx.search(s):
            return None
        return self.dates(s, dayfirst)

    def quick_date(self, s):
        """date_value for the millions of "2024-03-09 10:00:00 -0500" in an Apple Health export, the day cached."""
        if len(s) >= 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit() and (len(s) == 10 or not s[10].isdigit()):
            key = ("day", s[:10])
            if key not in self.cache:
                self.cache[key] = self.dates(s[:10])   # counts it
            elif not self.collecting:
                self.count("dates")
            return self.cache[key] + s[10:]
        dv = self.date_value(s)
        return s if dv is None else dv

    def birth(self, s, dayfirst=None):
        """A birth date: remembered in pass 1 (so the same date in free text is generalised too), then written as
        1 January of its moved year, in its own format."""
        if not isinstance(s, str) or not s.strip():
            return s
        m = HL7_TS.match(s.strip())
        found = []
        if m and m.group("d"):
            found.append((int(m.group("y")), int(m.group("m")), int(m.group("d"))))
        else:
            for dm in self.date_rx.finditer(s):
                g = dm.group
                if g("D1"):
                    found.append((int(g("y1")), int(g("m1")), int(g("d1"))))
                elif g("D2"):
                    found.append((int(g("y2")), int(g("m2")), int(g("d2"))))
                elif g("D3"):
                    a, b, y = int(g("a3")), int(g("b3")), g("y3")
                    yy = int(y) if len(y) == 4 else (2000 + int(y) if int(y) < 50 else 1900 + int(y))
                    df = g("s3") == "." or a > 12 or (b <= 12 and (self.dayfirst if dayfirst is None else dayfirst))
                    found.append((yy, b, a) if df else (yy, a, b))
                elif g("D5") and g("y5"):
                    found.append((int(g("y5")), self._month_no(g("n5")), int(g("d5"))))
                elif g("D6"):
                    y = g("y6")
                    yy = int(y) if len(y) == 4 else (2000 + int(y) if int(y) < 50 else 1900 + int(y))
                    found.append((yy, self._month_no(g("n6")), int(g("d6"))))
        if self.collecting:
            self.births.update(found)
            return s
        if m:   # an HL7 timestamp, of any precision
            return self.hl7(s, birth=True)
        return self.dates(s, dayfirst)

    def hl7(self, s, birth=False):
        """An HL7 timestamp (YYYY[MM[DD[HHMM[SS]]]][+-ZZZZ]) moved, keeping its precision."""
        m = HL7_TS.match(s.strip()) if isinstance(s, str) else None
        if not m:
            return (self.date_value(s) or s) if isinstance(s, str) else s
        if self.collecting:
            if birth and m.group("d"):
                self.births.add((int(m.group("y")), int(m.group("m")), int(m.group("d"))))
            return s
        y, mo, d = int(m.group("y")), m.group("m"), m.group("d")
        try:
            base = date(y, int(mo) if mo else 7, int(d) if d else (15 if mo else 1))
        except ValueError:
            if not self.collecting:
                self.report.unread_dates[self.rel] += 1
            return s
        new = base + self.offset
        if birth:
            self.count("birth dates")
            rest = m.group("rest")
            t = re.match(r"\d*(?:\.\d+)?", rest).group()
            out = f"{new.year:04d}" + ("01" if mo else "") + ("01" if d else "") + re.sub(r"\d", "0", t) + rest[len(t):]
            return s.replace(m.group(0), out, 1)
        self.count("dates")
        out = f"{new.year:04d}" + (f"{new.month:02d}" if mo else "") + (f"{new.day:02d}" if d else "") + m.group("rest")
        return s.replace(m.group(0), out, 1)

    # -------------------------------------------------------------- values
    def jitter_value(self, v):
        """A lab or vital-sign value moved by up to --jitter percent, keeping its decimals (and any < or >)."""
        if not self.jitter or self.collecting or isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            f = float(v)
            r = self.rng("jitter", repr(f))
            new = f * (1 + r.uniform(-self.jitter, self.jitter))
            self.count("values jittered")
            if isinstance(v, int):
                return int(round(new))
            decimals = len(repr(v).split(".")[1]) if "." in repr(v) and "e" not in repr(v) else 2
            return round(new, decimals)
        if not isinstance(v, str):
            return v
        m = NUMBER.match(v)
        if not m:
            return v
        cmp_, sign, whole, sep, frac, tail = m.groups()
        f = float(f"{sign}{whole}.{frac}" if frac else f"{sign}{whole}")
        r = self.rng("jitter", repr(f))
        new = f * (1 + r.uniform(-self.jitter, self.jitter))
        self.count("values jittered")
        text = f"{abs(new):.{len(frac) if frac else 0}f}"
        if sep == ",":
            text = text.replace(".", ",")
        return f"{cmp_ or ''}{'-' if new < 0 else ''}{text}{tail}"

    # -------------------------------------------------------------- free text
    def text(self, s, where=None, flag=True, dayfirst=None, names=True):
        """Free text with the names, identifiers, contact details and dates the tool recognises replaced. With
        names=False only the numbers, contact details and dates (for a name that was just made up)."""
        if not isinstance(s, str) or not s.strip():
            return s
        out = self.text_rx.sub(lambda m: self._text_match(m, dayfirst, names), s)
        if flag and where is not None and not self.collecting and _words(out) >= 6:
            self.report.kept_text.append((self.rel, where))
        return out

    def _text_match(self, m, dayfirst=None, names=True):
        kind, t = m.lastgroup, m.group()
        if not names and kind in ("KPH", "HON", "CRD", "LAB", "ORG", "KNM"):
            return t
        if kind == "EM":
            return self.email(t)
        if kind == "KID":
            return self.ident(t)
        if kind == "KPH":
            return self.street(t)
        if kind in ("SSN", "DIG"):
            return self.ident(t)
        if kind == "PH":
            return self.phone(t)
        if kind.startswith("D") and kind[1:].isdigit():
            return self._date_match(m, dayfirst)
        if kind == "LID":
            return m.group("lid_t") + m.group("lid_s") + self.ident(m.group("lid_v"))
        if kind == "HON":
            return m.group("hon_t") + self.person(m.group("hon_n"))
        if kind == "CRD":
            return self.person(m.group("crd_n")) + m.group("crd_t")
        if kind == "LAB":
            return m.group("lab_t") + self.either(m.group("lab_n"))
        if kind == "ORG":
            if self.keep_orgs:
                return t
            return self.org(m.group("org_n"), count=False) + m.group("org_k")
        if kind == "IDT":
            return self._code(t)
        if kind == "KNM":
            return self._known(t)
        return t

    def _code(self, t):
        """A token of letters and digits ("PX-26-0001", "Observation-A1B2", "export-20261115"): an identifier as a
        whole if pass 1 found it or it looks like one; a code made of words and dates piece by piece."""
        if not self.collecting and t in self.ids:
            return self.ident(t)
        pieces = re.findall(r"\d{4}-\d{2}-\d{2}|[^-]+|-", t)
        words = [p for p in pieces if p.isalpha() and len(p) >= 3]
        if len(pieces) == 1 or not (words or any(DATE_RE.fullmatch(p) for p in pieces)):
            if sum(c.isdigit() for c in t) >= 4 and not UNIT_NUMBER.match(t):
                return self.ident(t)
            return "-".join(self._known(p) for p in t.split("-"))
        out = []
        for p in pieces:
            if p == "-":
                out.append(p)
            elif DATE_RE.fullmatch(p):
                out.append(self.dates(p))
            elif (not self.collecting and p in self.ids) or (sum(c.isdigit() for c in p) >= 4 and not UNIT_NUMBER.match(p)):
                out.append(self.ident(p))
            else:
                out.append(self._known(p))
        return "".join(out)

    def _known(self, t):
        """A word or code replaced if pass 1 found it as a name or an identifier."""
        if self.collecting:
            return t
        if len(t) >= 4 and t in self.ids:
            return self.ident(t)
        w = _norm(t)
        if w in self.person_tokens:
            return _case_like(t, self._fake_word(w, self.person_tokens[w]))
        if w in self.place_tokens or (w in self.org_tokens and not self.keep_orgs):
            return _case_like(t, self._fake_word(w, "place"))
        return t

    def note(self, s, where=None):
        """A note or message: replaced with "Text removed." (HTML keeps its tags), or with --keep-notes cleaned."""
        if not isinstance(s, str) or not s.strip() or not re.search(r"[A-Za-z]", s):
            return s
        if self.notes != "replace":
            return self.text(s, where)
        if self.collecting:
            self.text(s)   # names in a note still count as names to find elsewhere
            return s
        self.count("notes replaced")
        if re.search(r"<[A-Za-z/][^>]*>", s):
            first = [True]

            def run(m):
                if not re.search(r"[A-Za-z]", m.group(2)):
                    return m.group(0)
                lead = re.match(r"\s*", m.group(2)).group()
                out = m.group(1) + lead + (REMOVED if first[0] else "")
                first[0] = False
                return out
            return HTML_TEXT.sub(run, s)
        lead, trail = re.match(r"\s*", s).group(), re.search(r"\s*$", s).group()
        return lead + REMOVED + trail

    def path(self, rel):
        """A path in the export with the names, numbers and dates in it changed the same way as in the records."""
        return "/".join(self.text(p, flag=False) for p in rel.split("/"))

    # ============================================================== JSON
    def json_doc(self, data):
        return self._walk(data, (), None, None)

    def _key(self, k):
        if not isinstance(k, str) or self.collecting:
            return k
        if len(k) >= 4 and k in self.ids:
            return self.ident(k)
        dv = self.date_value(k, compact=False)
        return dv if dv is not None else k

    def _walk(self, d, path, rt, key):
        if not isinstance(d, dict):
            return self._field(key, d, {}, path, rt)
        rt = d.get("resourceType") if isinstance(d.get("resourceType"), str) else rt
        kl = (key or "").lower() if isinstance(key, str) else ""
        keys = set(d)
        if keys & {"given", "family"} and keys <= {"use", "text", "family", "given", "prefix", "suffix", "period", "id",
                                                   "extension"}:
            return self._human_name(d, path, rt)
        if (kl in ("address", "valueaddress", "addr", "homeaddress", "mailingaddress", "billingaddress")
                or (keys & {"line", "postalCode", "city"} and keys <= {"use", "type", "text", "line", "city",
                                                                      "district", "state", "postalCode", "country",
                                                                      "period", "id", "extension"})):
            return self._address(d, path, rt)
        if isinstance(d.get("system"), str) and d.get("system") in ("phone", "fax", "email", "sms", "pager", "url",
                                                                       "other") and "value" in d:
            out = {}
            for k, v in d.items():
                if k == "value":
                    out[k] = self.email(v) if d["system"] == "email" else (v if d["system"] == "url" else self.phone(v))
                elif k in ("system", "use", "rank"):
                    out[k] = v
                else:
                    out[k] = self._field(k, v, d, path + (k,), rt)
            return out
        if "contentType" in d and ("data" in d or "url" in d):
            return self._attachment(d, path, rt)
        if (kl in ("identifier", "identifiers") or "identifier" in [p for p in path if isinstance(p, str)][-2:]) \
                and "value" in d and not isinstance(d.get("value"), (dict, list)):
            sys_ = str(d.get("system") or "")
            out = {}
            for k, v in d.items():
                if k == "value":
                    if self.keep_orgs and rt in ORG_RT:
                        out[k] = v
                    else:
                        out[k] = self.ident(v, kind="npi" if "npi" in sys_.lower() else "id")
                else:
                    out[self._key(k)] = self._field(k, v, d, path + (k,), rt)
            return out
        if kl == "text" and "div" in d:
            out = dict(d)
            out["div"] = self.note(d["div"], self._where(path + ("div",)))
            return out
        out = {}
        for k, v in d.items():
            out[self._key(k)] = self._field(k, v, d, path + (k,), rt)
        return out

    def _human_name(self, d, path, rt):
        out = {}
        for k, v in d.items():
            if k in ("given", "family"):
                out[k] = ([self.person(x, count=False, role=k) for x in v] if isinstance(v, list)
                          else self.person(v, count=False, role=k))
            elif k == "text":
                out[k] = self.person(v, count=False)
            elif k in ("prefix", "suffix", "use"):
                out[k] = v
            else:
                out[k] = self._field(k, v, d, path + (k,), rt)
        self.count("people's names")
        return out

    def _address(self, d, path, rt):
        out = {}
        for k, v in d.items():
            kl = k.lower()
            if kl in ("line", "streetaddressline"):
                out[k] = [self.street(x) for x in v] if isinstance(v, list) else self.street(v)
            elif kl in ("city", "district", "county"):
                out[k] = self.city(v)
            elif kl in ("postalcode", "zip", "zipcode", "postcode"):
                out[k] = self.postal(v)
            elif kl == "text":
                out[k] = self.address_text(v)
            elif kl in ("state", "country", "use", "type"):
                out[k] = v
            else:
                out[k] = self._field(k, v, d, path + (k,), rt)
        return out

    def _attachment(self, d, path, rt):
        out = {}
        kind = media_kind(d.get("contentType"))
        for k, v in d.items():
            if k == "data" and isinstance(v, str) and v:
                out[k] = self._data(v, kind, path + (k,))
            elif k == "url" and isinstance(v, str) and not re.match(r"(?i)^[a-z][a-z0-9+.-]*:", v):
                out[k] = self._url_path(v)
            elif k == "hash":
                continue   # the content changed
            elif k == "size":
                out[k] = v   # set below when the data changed
            elif k == "title":
                out[k] = self.text(v, flag=False)
            elif k == "creation":
                out[k] = self.date_value(v) or v
            else:
                out[k] = self._field(k, v, d, path + (k,), rt)
        if "data" in out and "size" in out and not self.collecting:
            try:
                out["size"] = len(base64.b64decode(out["data"]))
            except ValueError:
                pass
        return out

    def _data(self, b64, kind, path):
        """Inline base64 content (a FHIR attachment, a C-CDA nonXMLBody): text cleaned like a note, a document
        replaced by a placeholder of its type."""
        if self.collecting and kind != "text":
            return b64
        try:
            raw = base64.b64decode(b64, validate=False)
        except ValueError:
            raw = b""
        if kind == "text":
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            new = self.note(text, self._where(path))
            return b64 if self.collecting else base64.b64encode(new.encode("utf-8")).decode("ascii")
        self.count("documents replaced")
        return base64.b64encode(PLACEHOLDERS[kind]() if kind in PLACEHOLDERS else b"").decode("ascii")

    def _url_path(self, v):
        base, frag = (v.split("#", 1) + [None])[:2]
        new = self.path(base)
        return new + ("#" + frag if frag is not None else "")

    @staticmethod
    def _where(path):
        out = ""
        for p in path:
            out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
        return out or "(top)"

    @staticmethod
    def _id_key(k):
        if not isinstance(k, str):
            return False
        kl = k.lower().replace("_", "").replace("-", "")
        if kl in ID_KEYS:
            return True
        if PHONE_KEY.search(k) or k.lower() in ("id", "resourcetype"):
            return False
        return bool(ID_SUFFIX.search(k)) and not k.lower().endswith(("paid", "valid", "void", "grid"))

    def _context(self, path, rt, parent):
        """"person", "org", "either" or None: whose name a "name" or "display" field holds, from the keys above it."""
        if "resourceType" in parent and rt in PERSON_RT:
            return "person"
        if "resourceType" in parent and rt in ORG_RT:
            return "org"
        seen = 0
        for p in reversed(path[:-1]):
            if isinstance(p, int) or (len(p) <= 4 and any(c.isdigit() for c in p)) or re.fullmatch(r"[A-Za-z]{0,3}[-_]?\d+", p):
                continue
            if PERSON_HINT.match(p):
                return "either"
            if ORG_HINT.match(p):
                return "org"
            seen += 1
            if seen >= 2:
                break
        return None

    def _field(self, k, x, parent, path, rt):
        if isinstance(x, dict):
            return self._walk(x, path, rt, k)
        if isinstance(x, list):
            return [self._field(k, v, parent, path + (i,), rt) for i, v in enumerate(x)]
        if x is None or isinstance(x, bool):
            return x
        kl = k.lower() if isinstance(k, str) else ""
        kn = kl.replace("_", "").replace("-", "").replace(" ", "")
        if isinstance(x, (int, float)):
            if self._id_key(k) and not (rt and kl == "id"):
                return self.ident(x)
            if DATE_KEY.search(kl) and isinstance(x, int) and 10 ** 9 <= abs(x) < 10 ** 14:   # epoch seconds or ms
                if self.collecting:
                    return x
                self.count("dates")
                return x + self.offset.days * 86400 * (1000 if abs(x) >= 10 ** 11 else 1)
            if self.jitter and kn in VALUE_KEYS and not any(isinstance(p, str) and NOT_VALUE_CTX.search(p) for p in path):
                return self.jitter_value(x)
            return x
        if not isinstance(x, str) or not x.strip():
            return x
        where = self._where(path)
        if BIRTH_KEY.search(kn):
            return self.birth(x)
        if kl == "id" and rt and "resourceType" in parent:
            return self.ident(x)
        if kl in ("reference", "fullurl"):
            return self._reference(x)
        if kn == "npi" or self._id_key(k):
            if self.keep_orgs and (rt in ORG_RT or self._context(path, rt, parent) == "org"):
                return x
            return self.ident(x, kind="npi" if "npi" in kn else "id")
        if PHONE_KEY.search(kn) and not kn.endswith(("type", "use")):
            return self.phone(x)
        if EMAIL_KEY.search(kn):
            return self.email(x)
        if STREET_KEY.match(kn):
            return self.address_text(x) if kn in ("address", "addr", "streetaddress") else self.street(x)
        if CITY_KEY.match(kn):
            return self.city(x)
        if POSTAL_KEY.match(kn):
            return self.postal(x)
        if kn in KEEP_KEYS or kn.endswith(KEEP_SUFFIXES):
            if kn in ("url",) and not re.match(r"(?i)^[a-z][a-z0-9+.-]*:", x) and "/" in x:
                return self._url_path(x)
            return x
        if kn in PERSON_NAME_KEYS:
            return self.person(x)
        if kn in EITHER_KEYS:
            return self.either(x)
        if kn in ORG_KEYS:
            return self.org(x)
        if kn in ("name", "display", "displayname", "fullname", "title") or kn.endswith("name"):
            if kn == "display" and ("code" in parent or "system" in parent):
                return self.text(x, flag=False)
            ctx = self._context(path, rt, parent) if kn in ("name", "display", "displayname", "fullname") else None
            if ctx is None and kn.endswith("name") and kn not in ("name", "displayname"):
                stem = kn[:-4]
                if PERSON_HINT.match(stem) or stem in ("patient", "member", "provider", "doctor", "user", "employee"):
                    ctx = "either"
                elif ORG_HINT.match(stem):
                    ctx = "org"
            if ctx == "person":
                return self.person(x)
            if ctx == "org":
                return self.org(x)
            if ctx == "either":
                return self.either(x)
        dv = self.date_value(x, compact=bool(DATE_KEY.search(kl)))
        if dv is not None:
            return dv
        if not self.collecting and len(x) >= 4 and x in self.ids:
            return self.ident(x)
        if self.jitter and kn in VALUE_KEYS and NUMBER.match(x) and \
                not any(isinstance(p, str) and NOT_VALUE_CTX.search(p) for p in path):
            return self.jitter_value(x)
        if kn in NOTE_KEYS or (kn in ("text", "value", "content", "html", "string") and any(
                isinstance(p, str) and p.lower() in NOTE_CTX for p in path[-4:-1])):
            return self.note(x, where)
        return self.text(x, where)

    def _reference(self, x):
        """A FHIR reference or fullUrl: the id at its end changed as the resource's own id is."""
        m = re.match(r"^(.*?[/:])([^/:]+?)(/_history/[^/]+)?$", x)
        if not m or x.startswith("#"):
            return x
        return m.group(1) + self.ident(m.group(2)) + (m.group(3) or "")


# ------------------------------------------------------------------ XML (streamed with expat)

TIME_ELEMENTS = {"effectiveTime", "low", "high", "center", "time", "birthTime", "deceasedTime", "expectedUseTime",
                 "useablePeriod", "validTime"}
PERSON_PARENTS = {"patient", "assignedPerson", "guardianPerson", "associatedPerson", "relatedPerson", "informant",
                  "person", "subjectPerson", "maintainingPerson", "intendedRecipient"}
ORG_PARENTS = {"representedOrganization", "representedCustodianOrganization", "serviceProviderOrganization",
               "providerOrganization", "scopingOrganization", "wholeOrganization", "organization",
               "manufacturerOrganization", "receivedOrganization", "assignedCustodian", "location",
               "healthCareFacility", "playingDevice"}
XML_KEEP_ATTRS = {"code", "codeSystem", "codeSystemName", "codeSystemVersion", "classCode", "moodCode", "typeCode",
                  "unit", "type", "use", "nullFlavor", "negationInd", "inversionInd", "operator",
                  "institutionSpecified", "representation", "mediaType", "ID", "IDREF", "lang", "contextConductionInd",
                  "determinerCode", "key", "workoutActivityType", "durationUnit", "totalDistanceUnit",
                  "totalEnergyBurnedUnit", "sourceVersion", "locale", "assigningAuthorityName", "displayable",
                  "contextControlCode", "styleCode", "href", "schemaLocation", "noNamespaceSchemaLocation"}
APPLE_DATES = {"startDate", "endDate", "creationDate", "dateComponents", "date"}
APPLE_RECORD = {"Record", "Workout", "Correlation", "ActivitySummary", "WorkoutEvent", "WorkoutStatistics", "MetadataEntry",
                "InstantaneousBeatsPerMinute", "HeartRateVariabilityMetadataList", "WorkoutRoute", "FileReference",
                "ClinicalRecord", "Audiogram", "SensitivityPoint", "VisionPrescription", "Me", "ExportDate"}
KNOWN_XML = {"ClinicalDocument", "HealthData", "SubmitObjectsRequest"}   # C-CDA, Apple Health, an XDM package's list
DOCTYPE = re.compile(r"<!DOCTYPE\s+[A-Za-z_][\w.:-]*(?:\s+(?:SYSTEM|PUBLIC)\s+(?:\"[^\"]*\"|'[^']*')(?:\s+(?:\"[^\"]*\"|'[^']*'))?)?"
                     r"\s*(?:\[(?P<subset>.*?)\]\s*)?>", re.S)


def _local(q):
    return q.rsplit(":", 1)[-1]


def _esc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#13;")


def _esc_attr(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("\t", "&#9;").replace("\n", "&#10;").replace("\r", "&#13;"))


class XmlFile:
    """One XML file: every element, attribute and piece of text re-written in order, with the rules for C-CDA
    (names, addresses, telecoms, ids, HL7 timestamps, narrative), Apple Health (dates, sources, the owner's birth
    date) and anything else (dates, free text). Comments are left out; a DOCTYPE is kept only without entities."""

    def __init__(self, an, src, dst):
        self.an, self.src, self.dst = an, src, dst
        self.stack, self.buf, self.pending = [], [], None
        self.out = None
        self.root = None

    def run(self):
        with open(self.src, "rb") as f:
            head = f.read(MB)
        text_head = self._decode_head(head)
        doctype = None
        m = DOCTYPE.search(text_head)
        if re.search(r"<!\s*ENTITY", text_head, re.I):
            raise Skip("it declares XML entities, which can be used to attack the reader, so it wasn't read")
        if m:
            doctype = m.group(0)
        p = xml.parsers.expat.ParserCreate()
        p.ordered_attributes = True
        p.specified_attributes = True   # only what the file says, not a DTD's defaults
        p.buffer_text = True
        p.buffer_size = 1 << 16
        p.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
        p.StartElementHandler = self.start
        p.EndElementHandler = self.end
        p.CharacterDataHandler = self.chars
        p.ProcessingInstructionHandler = self.pi
        p.XmlDeclHandler = self.decl
        p.StartDoctypeDeclHandler = lambda *a: self.write((doctype + "\n") if doctype else "")
        p.EntityDeclHandler = self._refuse_entity
        p.UnparsedEntityDeclHandler = self._refuse_entity
        p.ExternalEntityRefHandler = self._refuse_entity
        self.out = None if self.an.collecting else open(self.dst, "w", encoding="utf-8", newline="")
        try:
            with open(self.src, "rb") as f:
                while piece := f.read(MB):
                    p.Parse(piece, False)
                p.Parse(b"", True)
        except xml.parsers.expat.ExpatError as e:
            raise Skip(f"it isn't well-formed XML ({e})") from None
        finally:
            if self.out:
                self.out.close()
        return self.root

    @staticmethod
    def _decode_head(head):
        if head.startswith((b"\xff\xfe", b"\xfe\xff")):
            return head.decode("utf-16", "replace")
        return head.decode("utf-8", "replace")

    def _refuse_entity(self, *a):
        raise Skip("it declares XML entities, which can be used to attack the reader, so it wasn't read")

    def write(self, s):
        if self.out and s:
            self.out.write(s)

    def decl(self, version, encoding, standalone):
        self.write('<?xml version="%s" encoding="UTF-8"%s?>\n' % (version or "1.0",
                                                                  ' standalone="yes"' if standalone == 1 else ""))

    def pi(self, target, data):
        self.flush()
        self._close_pending()
        self.write(f"<?{target} {data}?>" + ("" if self.stack else "\n"))

    def _close_pending(self):
        if self.pending is not None:
            self.write(self.pending + ">")
            self.pending = None

    def chars(self, data):
        self.buf.append(data)

    def flush(self):
        if not self.buf:
            return
        run = "".join(self.buf)
        self.buf = []
        if run.strip():
            run = self.text_run(run)
        self._close_pending()
        self.write(_esc_text(run))

    def start(self, name, attrs):
        self.flush()
        self._close_pending()
        local = _local(name)
        if self.root is None:
            self.root = local
        pairs = list(zip(attrs[::2], attrs[1::2]))
        ad = {_local(k): v for k, v in pairs}
        entry = {"q": name, "l": local, "a": ad, "id": "ID" in ad or bool(self.stack and self.stack[-1]["id"])}
        self.stack.append(entry)
        new = [(k, self.attr(entry, _local(k), k, v)) for k, v in pairs]
        if self.out:
            self.pending = "<" + name + "".join(f' {k}="{_esc_attr(v)}"' for k, v in new)

    def end(self, name):
        self.flush()
        if self.pending is not None:
            self.write(self.pending + "/>")
            self.pending = None
        else:
            self.write(f"</{name}>")
        self.stack.pop()

    # -------------------------------------------------------------- classification
    def _has(self, *names):
        return any(e["l"] in names for e in self.stack)

    def _parent(self, n=1):
        return self.stack[-1 - n]["l"] if len(self.stack) > n else ""

    def _name_kind(self):
        """For a <name> element: "person", "org", "place" or None (a drug or device name: kept)."""
        parent = self._parent(1)
        if parent in PERSON_PARENTS:
            return "person"
        if parent == "playingEntity":
            return "org" if self.stack[-2]["a"].get("classCode") == "PLC" else None
        if parent in ORG_PARENTS or parent.endswith("Organization"):
            return "org"
        if parent == "place":
            return "place"
        return None

    def attr(self, e, kl, k, v):
        an, l, a = self.an, e["l"], e["a"]
        if k.startswith("xmlns") or not v:
            return v
        if l == "telecom" and kl == "value":
            return an.contact(v)
        if l in ("id", "setId"):
            if kl == "extension":
                org = self._has(*ORG_PARENTS) or self._parent(1).endswith("Organization")
                if an.keep_orgs and org:
                    return v
                return an.ident(v, kind="npi" if a.get("root") == "2.16.840.1.113883.4.6" else "id")
            if kl == "root":
                if v.startswith("2.16.840.1.113883.") or (an.keep_orgs and self._has(*ORG_PARENTS)):
                    return v
                if re.fullmatch(r"[\d.]+", v):
                    return self._oid(v)
                return an.ident(v)
        if kl == "value" and "unit" not in a and (l in TIME_ELEMENTS or str(a.get("type", "")).endswith("TS")):
            if HL7_TS.match(v.strip()):
                return an.birth(v) if l == "birthTime" else an.hl7(v)
        if kl == "value" and l == "value" and a.get("type") == "PQ" and not self._has("referenceRange"):
            return an.jitter_value(v)
        if l == "Me" and "DateOfBirth" in kl:
            return an.birth(v)
        if kl in APPLE_DATES or (l == "ExportDate" and kl == "value"):
            return an.quick_date(v)
        if l == "ClinicalRecord" and kl == "sourceName":   # a Health Records entry: the health system it came from
            return an.org(v)
        if l == "ClinicalRecord" and kl == "sourceURL" and not an.keep_orgs:   # that health system's own server
            return re.sub(r"(?i)^(https?://)[^/]+", r"\1fhir.example.org", v)
        if kl in ("sourceName", "device"):
            return an.device(v)
        if l == "Record" and kl == "value":
            return an.jitter_value(v)
        if kl == "path" and l == "FileReference":
            return an.path(v)
        if kl in XML_KEEP_ATTRS or kl in ("root", "extension", "type"):
            return v
        if kl == "value" and l in APPLE_RECORD:
            dv = an.date_value(v)
            return dv if dv is not None else an.text(v, flag=False)
        dv = an.date_value(v, compact=False)
        if dv is not None:
            return dv
        return an.text(v, flag=False)

    def _oid(self, v):
        """A made-up OID under 2.25 (UUID-derived OIDs) for one that names an organisation's own system."""
        an = self.an
        if an.collecting:
            return v
        if v not in an.ids or not an.ids[v]:
            r = an.rng("oid", v)
            an.ids[v] = "2.25." + str(r.getrandbits(100))
        an.count("identifiers")
        return an.ids[v]

    def text_run(self, run):
        an, e = self.an, self.stack[-1] if self.stack else None
        if e is None:
            return run
        l = e["l"]
        lead, core, trail = re.match(r"^(\s*)(.*?)(\s*)$", run, re.S).groups()
        if l in ("given", "family", "prefix", "suffix") and self._parent(1) == "name":
            new = core if l in ("prefix", "suffix") else an.person(core, role=l)
        elif l == "name":
            kind = self._name_kind()
            new = {"person": an.person, "org": an.org, "place": an.city}.get(kind, lambda s: an.text(s, flag=False))(core)
        elif self._has("addr"):
            new = {"streetAddressLine": an.street, "city": an.city, "county": an.city, "postalCode": an.postal,
                   "state": lambda s: s, "country": lambda s: s, "addr": an.address_text,
                   "houseNumber": an.ident}.get(l, an.street)(core)
        elif l == "text" and e["a"].get("representation") == "B64":
            new = an._data(core, media_kind(e["a"].get("mediaType")), (self.where(),))
        elif self._has("text") and self._has("section", "nonXMLBody", "act", "observation", "substanceAdministration",
                                               "encounter", "procedure", "supply", "organizer"):
            in_table = self._has("td", "th", "item", "caption") or e["id"]
            if an.notes == "replace" and not in_table and _words(core) >= 6 and self._has("section", "nonXMLBody"):
                new = an.note(core, self.where())
            else:
                new = an.text(core, self.where() if _words(core) >= 6 else None)
        elif self.root == "HealthData":
            new = an.text(core, flag=False)
        else:
            new = an.date_value(core) or an.text(core, self.where())
        return lead + new + trail

    def where(self):
        return "/".join(e["l"] for e in self.stack[-4:])


# ------------------------------------------------------------------ CSV and text

def read_text(path):
    """(text, encoding, bom, newline) for a text file: UTF-8 (with or without BOM), UTF-16, or Windows-1252."""
    if os.path.getsize(path) > MAX_WHOLE:
        raise Skip(f"it is larger than {MAX_WHOLE // MB} MB, more than this tool reads as text")
    with open(path, "rb") as f:
        raw = f.read()
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if raw.startswith(bom):
            text = raw[len(bom):].decode(enc, "replace")
            break
    else:
        bom = b""
        try:
            text, enc = raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            text, enc = raw.decode("cp1252", "replace"), "cp1252"
    newline = "\r\n" if "\r\n" in text[:65536] else "\n"
    return text, enc, bom, newline


def write_text(path, text, enc, bom):
    with open(path, "wb") as f:
        f.write(bom + text.encode(enc, "replace"))


def sniff_delimiter(text):
    """The delimiter of a table (",", tab, ";" or "|"), or None if the text isn't one."""
    lines = [ln for ln in text.splitlines()[:40] if ln.strip()]
    if len(lines) < 2:
        return None
    best, best_score = None, 0
    for d in ("\t", ",", ";", "|"):
        counts = [ln.count(d) for ln in lines]
        common = collections.Counter(c for c in counts if c).most_common(1)
        if not common:
            continue
        n, hits = common[0]
        if n >= 1 and hits >= max(2, len(lines) * 0.6) and hits * n > best_score:
            best, best_score = d, hits * n
    return best


def csv_columns(header):
    kinds = []
    for h in header:
        h = (h or "").strip()
        if CSV_BIRTH.search(h):
            k = "birth"
        elif CSV_PHONE.search(h):
            k = "phone"
        elif CSV_EMAIL.search(h):
            k = "email"
        elif CSV_POSTAL.search(h):
            k = "postal"
        elif CSV_CITY.search(h):
            k = "city"
        elif CSV_STREET.search(h):
            k = "street"
        elif CSV_PERSON.search(h) and not CSV_NOT_PERSON.search(h):
            k = "either"
        elif CSV_ORG.search(h) and not CSV_NOT_PERSON.search(h):
            k = "org"
        elif CSV_ID.search(h) and not re.search(r"(?i)code|phone|type|status", h):
            k = "id"
        elif CSV_DATE.search(h) and not re.search(r"(?i)\bunit|\bcode", h):
            k = "date"
        elif CSV_VALUE.search(h):
            k = "value"
        elif CSV_NOTE.search(h):
            k = "note"
        else:
            k = "text"
        kinds.append(k)
    return kinds


def dayfirst_of(values):
    """True, False or None (can't tell) for a column of slash or dot dates."""
    first = second = 0
    for v in values:
        m = re.match(r"^\s*(\d{1,2})([/.-])(\d{1,2})\2\d{2,4}", v or "")
        if not m:
            continue
        if m.group(2) == ".":
            return True
        if int(m.group(1)) > 12:
            first += 1
        elif int(m.group(3)) > 12:
            second += 1
    return True if first > second else (False if second > first else None)


def apple_ecg_rows(an, rows):
    """An Apple Health export's ECG file (#182, electrocardiograms/*.csv): ten "key,value" lines in Apple's order (Name,
    Date of Birth, Recorded Date, Classification, Symptoms, Software Version, Device, Sample Rate, Lead, Unit; named
    in the export's language), a blank line, then one sample a line. The rows cleaned, or None when it isn't one."""
    if len(rows) < 12 or not all(len(r) == 2 for r in rows[:10]) or any(c.strip() for c in rows[10]):
        return None
    if not all(len(r) == 1 and re.fullmatch(r"\s*-?\d+(?:[.,]\d+)?\s*", r[0]) for r in rows[11:200] if r):
        return None
    out = [list(r) for r in rows]
    out[0][1] = an.person(rows[0][1])
    out[1][1] = an.birth(rows[1][1])
    out[2][1] = an.date_value(rows[2][1]) or an.text(rows[2][1], flag=False)
    return out   # the symptoms are Apple's own list's words; classification, device and the rest are the watch's


def csv_file(an, dst, text, enc, bom, newline, delim):
    csv.field_size_limit(MAX_WHOLE)
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
    if (ecg := apple_ecg_rows(an, rows)) is not None:   # (#182)
        if not an.collecting:
            buf = io.StringIO(newline="")
            csv.writer(buf, delimiter=delim, lineterminator=newline, quoting=csv.QUOTE_MINIMAL).writerows(ecg)
            write_text(dst, buf.getvalue(), enc, bom)
        return
    lengths = collections.Counter(len(r) for r in rows if len(r) >= 2)
    width = lengths.most_common(1)[0][0] if lengths else 0
    hi = next((i for i, r in enumerate(rows) if len(r) == width), None)
    out = []
    kinds = csv_columns(rows[hi]) if hi is not None else []
    header = rows[hi] if hi is not None else []
    dfirst = {}
    for c, k in enumerate(kinds):
        if k in ("date", "birth"):
            dfirst[c] = dayfirst_of(r[c] for r in rows[hi + 1:] if len(r) > c)
    for i, row in enumerate(rows):
        if hi is None or i < hi:
            out.append([an.text(c, f"line {i + 1}") for c in row])
            continue
        if i == hi:
            out.append([an.text(c, flag=False) for c in row])
            continue
        new = []
        for c, cell in enumerate(row):
            k = kinds[c] if c < len(kinds) else "text"
            where = f"line {i + 1}, column {header[c].strip() if c < len(header) else c + 1}"
            if not cell.strip():
                new.append(cell)
            elif k == "birth":
                new.append(an.birth(cell, dfirst.get(c)))
            elif k == "date":
                dv = an.date_value(cell, dfirst.get(c))
                if dv is None and not an.collecting and re.search(r"\d", cell) and not re.fullmatch(
                        r"\s*\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?\s*", cell):
                    an.report.unread_dates[an.rel] += 1
                new.append(dv if dv is not None else an.text(cell, flag=False, dayfirst=dfirst.get(c)))
            elif k == "phone":
                new.append(an.phone(cell))
            elif k == "email":
                new.append(an.email(cell))
            elif k == "street":
                new.append(an.address_text(cell))
            elif k == "city":
                new.append(an.city(cell))
            elif k == "postal":
                new.append(an.postal(cell))
            elif k == "either":
                new.append(an.either(cell))
            elif k == "org":
                new.append(an.org(cell))
            elif k == "id":
                new.append(an.ident(cell))
            elif k == "value":
                new.append(an.jitter_value(cell))
            elif k == "note":
                new.append(an.note(cell, where))
            else:
                new.append(an.text(cell, where))
        out.append(new)
    if an.collecting:
        return
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=delim, lineterminator=newline, quoting=csv.QUOTE_MINIMAL)
    for r in out:
        w.writerow(r)
    result = buf.getvalue()
    if not text.endswith(("\n", "\r")) and result.endswith(newline):
        result = result[:-len(newline)]
    write_text(dst, result, enc, bom)


def text_file(an, dst, text, enc, bom):
    if an.notes == "replace":
        new = an.note(text, "the whole file")
    else:
        new = an.text(text, "the whole file")
    if not an.collecting:
        write_text(dst, new, enc, bom)


# ------------------------------------------------------------------ files and folders

def file_kind(rel, path):
    ext = os.path.splitext(rel)[1].lower()
    base = rel.rsplit("/", 1)[-1]
    if base in (".DS_Store", "Thumbs.db", "desktop.ini"):
        return "junk"
    if ext == ".zip":
        return "zip" if zipfile.is_zipfile(path) else "unknown"
    if ext in XML_EXT:
        return "xml"
    if ext in JSON_EXT:
        return "json"
    if ext in NDJSON_EXT:
        return "ndjson"
    if ext in CSV_EXT:
        return "csv"
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    if ext in OTHER_DOC_EXT:
        return "doc"
    if ext in LOCATION_EXT:
        return "location"
    if ext in ARCHIVE_EXT:
        return "archive"
    with open(path, "rb") as f:
        head = f.read(4096)
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        head = head.decode("utf-16", "replace").encode("utf-8")
    if b"\0" in head:
        return "unknown"
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    html = ext in (".html", ".htm", ".xhtml") or stripped[:15].lower().startswith((b"<!doctype html", b"<html"))
    if stripped.startswith(b"<?xml") or (stripped.startswith(b"<") and not html):
        return "xml"
    if stripped.startswith((b"{", b"[")) and ext not in TEXT_EXT:
        return "json"
    return "text"


class Tree:
    """The files of the export: {rel: path}, and the trees of the zips inside it, unpacked."""

    def __init__(self, files):
        self.files, self.zips = files, {}
        self.skipped = {}             # rel -> why it is left out
        self.unknown, self.not_json = set(), set()


class Run:
    def __init__(self, an):
        self.an = an
        self.report = an.report
        self.budget = Budget()
        self.work = tempfile.mkdtemp(prefix="anonymise-")
        self.n = 0

    def close(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _space(self):
        self.n += 1
        d = os.path.join(self.work, str(self.n))
        os.makedirs(d)
        return d

    def load(self, files, depth, prefix=""):
        tree = Tree(files)
        for rel, path in files.items():
            if file_kind(rel, path) == "zip":
                if depth >= MAX_DEPTH:
                    raise Refused(f"{prefix + rel} holds zips inside zips more than {MAX_DEPTH} deep.")
                tree.zips[rel] = self.load(unzip(path, self._space(), self.budget, prefix + rel), depth + 1,
                                           prefix + rel + "/")
        return tree

    def kinds(self, tree):
        out = collections.Counter()
        for rel, path in tree.files.items():
            out[file_kind(rel, path)] += 1
        for sub in tree.zips.values():
            out += self.kinds(sub)
        return out

    def process(self, tree, out_dir, prefix=""):
        """Pass 1 (collecting) reads every file; pass 2 writes the copy into out_dir. The report names files by their
        new names (prefix is the new path of the zip they are in), since the old ones may hold a name."""
        an = self.an
        written = set()
        for rel in sorted(tree.files):
            path = tree.files[rel]
            kind = file_kind(rel, path)
            if an.collecting:
                an.rel = prefix + rel
                try:
                    self.one(tree, rel, path, kind, None, prefix + rel)
                except Skip as e:
                    tree.skipped[rel] = str(e)
                continue
            new_rel = an.path(rel)
            if new_rel != rel:
                an.count("file names")
            base, ext = os.path.splitext(new_rel)
            n = 2
            while new_rel.casefold() in written:
                new_rel, n = f"{base}-{n}{ext}", n + 1
            written.add(new_rel.casefold())
            shown = an.rel = prefix + new_rel
            if rel in tree.skipped:
                self.report.leave_out(shown, tree.skipped[rel])
                continue
            if rel in tree.unknown:
                self.report.unknown.append(shown)
            if rel in tree.not_json:
                self.report.notes.append(f"{shown} isn't valid JSON: it was cleaned as plain text only.")
            dst = os.path.join(out_dir, *new_rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                self.one(tree, rel, path, kind, dst, shown)
            except Skip as e:
                if os.path.exists(dst):
                    os.remove(dst)
                self.report.leave_out(shown, str(e))
                continue
            if kind != "zip" and os.path.exists(dst):
                self.report.files += 1

    def one(self, tree, rel, path, kind, dst, shown):
        """Read (pass 1) or write (pass 2) one file. Reading problems leave the file out (Skip)."""
        try:
            self._one(tree, rel, path, kind, dst, shown)
        except RecursionError:
            raise Skip("it is nested too deeply to read") from None
        except csv.Error as e:
            raise Skip(f"it isn't a table this tool can read ({e})") from None
        except OSError as e:
            if not self.an.collecting:
                raise
            raise Skip(f"it couldn't be read ({e.strerror or e})") from None

    def _one(self, tree, rel, path, kind, dst, shown):
        an = self.an
        if kind == "zip":
            sub = tree.zips[rel]
            if an.collecting:
                self.process(sub, None, shown + "/")
                return
            tmp = self._space()
            self.process(sub, tmp, shown + "/")
            write_zip(tmp, dst)
        elif kind == "xml":
            root = XmlFile(an, path, dst).run()
            if an.collecting and root not in KNOWN_XML:
                tree.unknown.add(rel)
        elif kind in ("json", "ndjson"):
            self._json(tree, rel, path, kind, dst)
        elif kind in ("csv", "text"):
            text, enc, bom, newline = read_text(path)
            delim = "\t" if rel.lower().endswith((".tsv", ".tab")) else sniff_delimiter(text)
            if kind == "csv" or (delim and not rel.lower().endswith((".html", ".htm", ".xhtml", ".md"))):
                csv_file(an, dst, text, enc, bom, newline, delim or ",")
            else:
                text_file(an, dst, text, enc, bom)
        elif kind == "pdf" or kind == "image" or kind == "doc":
            if an.collecting:
                return
            ext = os.path.splitext(rel)[1].lower()
            data = _pdf() if kind == "pdf" else (PLACEHOLDERS[IMAGE_EXT[ext]]() if kind == "image" else b"")
            with open(dst, "wb") as f:
                f.write(data)
            an.count("documents replaced")
        elif kind == "location":
            raise Skip("a location track (where you went), which isn't needed to write an importer")
        elif kind == "archive":
            raise Skip("an archive this tool can't open (only zip): unpack it and run this on the folder")
        elif kind == "junk":
            raise Skip("a file the operating system adds, not records")
        else:
            raise Skip("a binary file this tool can't read or clean")

    def _json(self, tree, rel, path, kind, dst):
        an = self.an
        text, enc, bom, _ = read_text(path)
        if kind == "ndjson":
            out_lines = []
            for i, line in enumerate(text.splitlines(keepends=True)):
                body = line.rstrip("\r\n")
                if not body.strip():
                    out_lines.append(line)
                    continue
                try:
                    obj = json.loads(body)
                except ValueError:
                    raise Skip(f"line {i + 1} isn't JSON") from None
                new = an.json_doc(obj)
                out_lines.append(json.dumps(new, ensure_ascii=False, separators=(",", ":")) + line[len(body):])
            if not an.collecting:
                write_text(dst, "".join(out_lines), enc, bom)
            return
        try:
            obj = json.loads(text)
        except ValueError:
            if an.collecting:
                tree.not_json.add(rel)
            text_file(an, dst, text, enc, bom)
            return
        if an.collecting and not self._known_json(obj):
            tree.unknown.add(rel)
        new = an.json_doc(obj)
        if an.collecting:
            return
        indent = None
        m = re.search(r"\n([ \t]+)\S", text[:4096])
        if m:
            indent = "\t" if m.group(1).startswith("\t") else len(m.group(1))
        elif "\n" in text.strip():
            indent = 0
        body = json.dumps(new, ensure_ascii=False, indent=indent,
                          separators=(",", ": ") if indent is not None else (",", ":"))
        write_text(dst, body + ("\n" if text.endswith("\n") else ""), enc, bom)

    @staticmethod
    def _known_json(obj):
        if isinstance(obj, dict):
            keys = set(obj)
            return bool(keys & {"resourceType", "events", "groups", "details", "letters", "users", "list", "data",
                                "admissions", "departments"}) or any(isinstance(v, dict) and "messages" in v
                                                                     for v in obj.values())
        return isinstance(obj, list)


def write_zip(src_dir, dst):
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for d, dirs, names in os.walk(src_dir):
            dirs.sort()
            for n in sorted(names):
                p = os.path.join(d, n)
                rel = os.path.relpath(p, src_dir).replace(os.sep, "/")
                info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(p, "rb") as f, zf.open(info, "w") as out:
                    shutil.copyfileobj(f, out, MB)


# ------------------------------------------------------------------ the whole run

CANT = ("anonymise.py reads C-CDA and other XML, FHIR JSON and NDJSON, Apple Health exports, CSV and other "
        "text files, and zips of them.")


def anonymise(src, dst=None, *, seed=None, jitter=0.0, notes="replace", keep_orgs=False):
    """Write an anonymised copy of the export at src (a file, folder or zip) to dst, and return the report as a
    dict (Report.as_dict(), plus "output"). dst defaults to "<name>-anonymised" beside src. Raises Refused."""
    src = os.path.abspath(src)
    if not os.path.exists(src):
        raise Refused(f"There's nothing at {src}.")
    an = Anonymiser(seed=seed, jitter=jitter, notes=notes, keep_orgs=keep_orgs)
    run = Run(an)
    try:
        is_zip = os.path.isfile(src) and src.lower().endswith(".zip") and zipfile.is_zipfile(src)
        if os.path.isdir(src):
            files = walk(src, run.budget, an.report)
        elif is_zip:
            files = unzip(src, run._space(), run.budget, os.path.basename(src))
        elif os.path.isfile(src):
            run.budget.take(1, os.path.getsize(src))
            files = {os.path.basename(src): src}
        else:
            raise Refused(f"{src} isn't a file or a folder.")
        if not files:
            raise Refused("The export is empty.")
        tree = run.load(files, 1)
        kinds = run.kinds(tree)
        if not any(kinds.get(k) for k in RECORDS):
            if kinds.get("pdf") and sum(kinds.values()) == kinds["pdf"]:
                raise Refused("This is a PDF. anonymise.py can't clean PDFs: their text can't be changed reliably, "
                              "and a scan is a picture. Describe the report in the issue instead (the lab, the layout, "
                              "the column headings), or type a few made-up rows in the same layout. " + CANT)
            if kinds.get("doc") or kinds.get("image"):
                raise Refused("These are documents or pictures (a spreadsheet, a scan, a Word file...), which "
                              "anonymise.py can't clean. Save a spreadsheet as CSV and run this on that. " + CANT)
            raise Refused("Nothing here is in a format anonymise.py can read. " + CANT)
        run.process(tree, None)            # pass 1: find the names, identifiers and birth dates
        an.finish_collecting()
        base = os.path.basename(src.rstrip(os.sep))
        if dst is None:
            stem, ext = (os.path.splitext(base) if os.path.isfile(src) else (base, ""))
            dst = os.path.join(os.path.dirname(src), an.path(stem) + "-anonymised" + ext)
        dst = os.path.abspath(dst)
        if os.path.exists(dst):
            raise Refused(f"{dst} already exists: choose another name with -o, or remove it first.")
        if os.path.isdir(src) and (dst + os.sep).startswith(src + os.sep):
            raise Refused("The copy can't be written inside the export itself: choose another place with -o.")
        stage = tempfile.mkdtemp(prefix=".anonymise-", dir=os.path.dirname(dst) or ".")
        try:
            out_dir = os.path.join(stage, "out")
            os.makedirs(out_dir)
            run.process(tree, out_dir)     # pass 2: write the copy
            if os.path.isdir(src):
                os.rename(out_dir, dst)
            elif is_zip:
                write_zip(out_dir, dst)
            else:
                (only,) = [os.path.join(d, n) for d, _, ns in os.walk(out_dir) for n in ns] or [None]
                if only is None:
                    raise Refused("Nothing could be written: " + "; ".join(f"{r}: {w}" for r, w in an.report.left_out))
                os.rename(only, dst)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    finally:
        run.close()
    out = an.report.as_dict()
    out["output"] = dst
    out["_report"] = an.report
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Make an anonymised copy of a health-records export to share as a sample. Read "
                    "docs/anonymising-exports.md first, and look over the copy before you share it.")
    ap.add_argument("export", help="the export: a file, a folder or a zip")
    ap.add_argument("-o", "--output", help="where to write the copy (default: beside the export, named ...-anonymised)")
    ap.add_argument("--seed", help="make the run repeatable: the same export and seed give the same copy (keep it private)")
    ap.add_argument("--jitter", type=float, default=0.0, metavar="PERCENT",
                    help="also move lab and vital-sign values by up to this many percent (ranges are kept)")
    ap.add_argument("--keep-notes", action="store_true",
                    help="keep notes and messages, cleaned of the names, numbers and dates found, instead of "
                         "replacing them (read them yourself before sharing)")
    ap.add_argument("--keep-organisations", "--keep-organizations", action="store_true", dest="keep_orgs",
                    help="keep the names (and identifiers) of clinics, hospitals, labs and insurers")
    args = ap.parse_args(argv)
    if args.jitter < 0 or args.jitter > 50:
        print("anonymise.py: --jitter is a percentage between 0 and 50.", file=sys.stderr)
        return 2
    try:
        res = anonymise(args.export, args.output, seed=args.seed, jitter=args.jitter,
                        notes="scrub" if args.keep_notes else "replace", keep_orgs=args.keep_orgs)
    except Refused as e:
        print(f"anonymise.py: refused: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"anonymise.py: the copy couldn't be written: {e}", file=sys.stderr)
        return 1
    print(res["_report"].text(res["output"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
