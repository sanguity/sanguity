#!/usr/bin/env python3
"""Build the "Find your provider" directory (#93) from the vendors' published endpoint lists.

Run by a maintainer or by an install's admin, never by the app: the app only reads the files this writes and makes
no lookups of its own (server/directory/). Standard library only, so it runs on the host without the app's
dependencies.

  python3 tools/build_directory.py                 # fetch every list; write data/directory/providers.json.gz
  python3 tools/build_directory.py --bundled       # rewrite server/directory/providers.json (no network)
  python3 tools/build_directory.py --from epic-brands=brands.json --from oracle-millennium=oracle.json --out x.json.gz

`--out` defaults to $DATA_DIR/directory/providers.json.gz (DATA_DIR as in .env, else ./data), where the app looks
for it (SANGUITY_DATA inside the container). The app picks up a new file on the next search; no restart needed.

Sources, their terms, and what may be bundled (read November 2026):

- **Epic: User-access Brands bundle (R4)**, https://open.epic.com/Endpoints/Brands, listed on
  https://open.epic.com/MyApps/Endpoints. A FHIR Bundle of Organization (brands and their locations) and Endpoint
  resources, per the SMART App Launch User-access Brands spec
  (https://hl7.org/fhir/smart-app-launch/brands.html). About 97 MB as JSON: 1,315 brands, 98,043 locations and 830
  endpoints on 2026-11-15. The endpoints page asks apps not to query it at runtime and to download it regularly.
  The open.epic Terms of Use (https://open.epic.com/Home/TermsOfUse, updated 2025-10-31) let you "keep copies of
  the Materials for yourself", but distribute them "only by linking others directly to the Materials on the
  website". So it is **not bundled**: each install fetches its own copy with this script.
- **Oracle Health (Cerner) Millennium patient R4 endpoints**,
  https://github.com/oracle-samples/ignite-endpoints (oracle_health_fhir_endpoints/millennium_patient_r4_endpoints.json).
  A FHIR Bundle of Endpoint and Organization resources, 1,319 organisations, about 4.4 MB. The repository says
  "Copyright (c) 2023, 2025 Oracle and/or its affiliates" and has no licence file or grant, so it is **not
  bundled** either: fetched here only.
- **CMS / payers.** CMS publishes no single list of payers' patient-access endpoints (CMS-9115-F and CMS-0057-F
  require each payer to publish its own). The one CMS service, Medicare's Blue Button 2.0
  (https://bluebutton.cms.gov/api-documentation/), is a work of the US government (17 U.S.C. 105) and **is
  bundled**, its sandbox and production bases, below.
- **Vendor sandboxes**, written here by hand from each vendor's documentation (a base URL is a fact, not a copy of
  a list), **bundled**: Epic's (https://fhir.epic.com/Documentation?docId=testpatients) and Oracle Health's
  public sandbox (https://docs.oracle.com/en/industries/health/millennium-platform-apis/).

Each entry records its source and the date it was read. The file lists every source with whether it is in the
file, and why not when it isn't.
"""
import argparse, datetime, gzip, json, os, re, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED = os.path.join(ROOT, "server", "directory", "providers.json")
FORMAT = 1
UA = "sanguity-directory-build/1"   # a generic client name: nothing about the install

SOURCES = {
    "epic-brands": {
        "label": "Epic: User-access Brands bundle (R4)",
        "url": "https://open.epic.com/Endpoints/Brands",
        "terms": "https://open.epic.com/Home/TermsOfUse",
        "redistributable": False,
        "why": "open.epic's terms allow keeping a copy for yourself and sharing only by linking to it, so each install "
               "fetches its own copy.",
    },
    "oracle-millennium": {
        "label": "Oracle Health Millennium patient R4 endpoints",
        "url": "https://raw.githubusercontent.com/oracle-samples/ignite-endpoints/main/oracle_health_fhir_endpoints/"
               "millennium_patient_r4_endpoints.json",
        "terms": "https://github.com/oracle-samples/ignite-endpoints",
        "redistributable": False,
        "why": "The list is copyright Oracle with no licence granted, so each install fetches its own copy.",
    },
    "curated": {
        "label": "Vendor sandboxes and Medicare Blue Button 2.0, from the vendors' documentation",
        "url": None,
        "terms": "https://bluebutton.cms.gov/api-documentation/",
        "redistributable": True,
        "why": None,
    },
}
FETCHED = [k for k, s in SOURCES.items() if s["url"]]

# read 2026-11-15; each base answered its .well-known/smart-configuration that day
CURATED_DATE = "2026-11-15"
CURATED = [
    {"id": "epic:sandbox", "name": "Epic on FHIR sandbox (test patients)", "vendor": "epic", "kind": "sandbox",
     "fhir_base": "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/", "fhir_version": "4.0.1",
     "org": "Epic", "aliases": ["Epic sandbox", "fhir.epic.com", "test"], "places": [["Verona", "WI"]], "sandbox": True},
    {"id": "oracle:sandbox", "name": "Oracle Health (Cerner) patient sandbox", "vendor": "oracle", "kind": "sandbox",
     "fhir_base": "https://fhir-myrecord.cerner.com/r4/ec2458f2-1e24-41c8-b71b-0e701af7583d/", "fhir_version": "4.0.1",
     "org": "Oracle Health", "aliases": ["Cerner sandbox", "Millennium sandbox", "test"], "sandbox": True},
    {"id": "cms:bluebutton-sandbox", "name": "Medicare Blue Button 2.0 sandbox", "vendor": "cms-bluebutton",
     "kind": "sandbox", "fhir_base": "https://sandbox.bluebutton.cms.gov/v2/fhir/", "fhir_version": "4.0.1",
     "org": "Centers for Medicare & Medicaid Services", "aliases": ["CMS", "Medicare", "Blue Button", "test"],
     "sandbox": True},
    {"id": "cms:bluebutton", "name": "Medicare (Blue Button 2.0)", "vendor": "cms-bluebutton", "kind": "payer",
     "fhir_base": "https://api.bluebutton.cms.gov/v2/fhir/", "fhir_version": "4.0.1",
     "org": "Centers for Medicare & Medicaid Services", "aliases": ["CMS", "Medicare", "Blue Button"],
     "places": [["Baltimore", "MD"]]},
]


# ---------------------------------------------------------------- reading the lists

def _ident(ref):
    """The id a reference names: urn:uuid:<id>, Endpoint/<id> or a full URL ending in it."""
    return re.split(r"[/:]", ref or "")[-1]


def _place(addr):
    city, state = (addr.get("city") or "").strip(), (addr.get("state") or "").strip()
    return [city.title() if city.isupper() else city, state.upper()] if city or state else None


def _brand(org):
    """Logo and portal from the SMART brand extensions, when a list carries them (URLs only; never fetched)."""
    logo = portal = None
    for x in org.get("extension", []):
        subs = {s.get("url"): s for s in x.get("extension", [])}
        if x.get("url", "").endswith("organization-brand"):
            logo = (subs.get("brandLogo") or {}).get("valueUrl") or logo
        elif x.get("url", "").endswith("organization-portal"):
            portal = (subs.get("portalUrl") or {}).get("valueUrl") or portal
            logo = logo or (subs.get("portalLogo") or {}).get("valueUrl")
    return logo, portal


def _clean(entry):
    return {k: v for k, v in entry.items() if v not in (None, [], "", False)}


def _dedupe(items):
    seen, out = set(), []
    for i in items:
        k = json.dumps(i).lower()
        if i and k not in seen:
            seen.add(k)
            out.append(i)
    return out


def from_epic(bundle, retrieved):
    """One entry per primary brand (an Organization with an endpoint); its locations (partOf it) add names and
    places to search by."""
    res = [e.get("resource", {}) for e in bundle.get("entry", [])]
    endpoints = {r["id"]: r for r in res if r.get("resourceType") == "Endpoint" and r.get("status", "active") == "active"}
    brands, locs = {}, {}
    for r in res:
        if r.get("resourceType") != "Organization" or r.get("active") is False:
            continue
        if r.get("endpoint"):
            brands[r["id"]] = r
        elif r.get("partOf"):
            locs.setdefault(_ident(r["partOf"].get("reference")), []).append(r)
    out = []
    for oid, org in brands.items():
        ep = next((endpoints[_ident(x.get("reference"))] for x in org["endpoint"] if _ident(x.get("reference")) in endpoints), None)
        if not ep or not ep.get("address"):
            continue
        version = next((x.get("valueCode") for x in ep.get("extension", []) if x.get("url", "").endswith("endpoint-fhir-version")), None)
        bid = next((i.get("value") for i in org.get("identifier", []) if i.get("system", "").endswith("brand-identifier")), oid)
        logo, portal = _brand(org)
        children = locs.get(oid, [])
        name = org.get("name") or ep.get("name") or ""
        aliases = [org.get("alias", [])] + [[c.get("name")] for c in children]
        out.append(_clean({
            "id": f"epic:{bid}", "name": name, "vendor": "epic", "kind": "provider",
            "fhir_base": ep["address"].rstrip("/") + "/", "fhir_version": version,
            "org": ((ep.get("managingOrganization") or {}).get("display")),
            "aliases": sorted({a.strip() for grp in aliases for a in grp if a and a.strip() and a.strip().lower() != name.lower()}),
            "places": _dedupe([_place(a) for o in [org, *children] for a in o.get("address", [])]),
            "logo": logo, "portal": portal, "source": "epic-brands", "retrieved": retrieved}))
    return out


def from_oracle(bundle, retrieved):
    """One entry per Organization, each with one endpoint."""
    res = [e.get("resource", {}) for e in bundle.get("entry", [])]
    endpoints = {r["id"]: r for r in res if r.get("resourceType") == "Endpoint" and r.get("status", "active") == "active"}
    out = []
    for org in res:
        if org.get("resourceType") != "Organization":
            continue
        ep = next((endpoints[_ident(x.get("reference"))] for x in org.get("endpoint", []) if _ident(x.get("reference")) in endpoints), None)
        if not ep or not ep.get("address"):
            continue
        logo, portal = _brand(org)
        out.append(_clean({
            "id": f"oracle:{ep['id']}", "name": org.get("name", ""), "vendor": "oracle", "kind": "provider",
            "fhir_base": ep["address"].rstrip("/") + "/", "fhir_version": "4.0.1", "aliases": org.get("alias"),
            "places": _dedupe([_place(a) for a in org.get("address", [])]),
            "logo": logo, "portal": portal, "source": "oracle-millennium", "retrieved": retrieved}))
    return out


READERS = {"epic-brands": from_epic, "oracle-millennium": from_oracle}


def curated():
    return [_clean({**e, "source": "curated", "retrieved": CURATED_DATE}) for e in CURATED]


# ---------------------------------------------------------------- fetching and writing

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/fhir+json, application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def build(inputs, today=None):
    """inputs: {source: parsed bundle} for the fetched sources to include. Returns the directory document."""
    today = today or datetime.date.today().isoformat()
    entries = curated()
    sources = []
    for key, s in SOURCES.items():
        info = {"id": key, "label": s["label"], "url": s["url"], "terms": s["terms"], "redistributable": s["redistributable"]}
        if key == "curated":
            info.update(included=True, retrieved=CURATED_DATE)
        elif key in inputs:
            got = [e for e in READERS[key](inputs[key], today) if e["fhir_base"].startswith("https://")]   # tokens only over HTTPS
            entries += got
            info.update(included=True, retrieved=today, entries=len(got))
        else:
            info.update(included=False, why=s["why"] + " Run tools/build_directory.py to add it.")
        sources.append(info)
    entries.sort(key=lambda e: (e.get("sandbox", False), e["name"].lower(), e["id"]))
    return {"format": FORMAT, "built": today, "sources": sources, "entries": entries}


def write(doc, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    body = json.dumps(doc, ensure_ascii=False, separators=(",", ":") if path.endswith(".gz") else None,
                      indent=None if path.endswith(".gz") else 1).encode("utf-8")
    tmp = path + ".tmp"
    if path.endswith(".gz"):
        with gzip.GzipFile(tmp, "wb", mtime=0) as f:
            f.write(body)
    else:
        with open(tmp, "wb") as f:
            f.write(body + b"\n")
    os.replace(tmp, path)
    return os.path.getsize(path), len(body)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--bundled", action="store_true", help="write only what may be redistributed, to " + os.path.relpath(BUNDLED, ROOT))
    p.add_argument("--from", dest="files", action="append", default=[], metavar="SOURCE=FILE",
                   help="read a source from a file already downloaded instead of fetching it (" + ", ".join(FETCHED) + ")")
    p.add_argument("--only-files", action="store_true", help="fetch nothing: use only the --from files")
    p.add_argument("--out", help="where to write (.json.gz for the full list)")
    a = p.parse_args(argv)

    inputs = {}
    if not a.bundled:
        files = dict(f.split("=", 1) for f in a.files)
        unknown = set(files) - set(FETCHED)
        if unknown:
            p.error(f"unknown source(s): {', '.join(sorted(unknown))}")
        for key in FETCHED:
            if key in files:
                with open(files[key], encoding="utf-8") as f:
                    inputs[key] = json.load(f)
            elif not a.only_files:
                print(f"Fetching {SOURCES[key]['label']}: {SOURCES[key]['url']}", file=sys.stderr)
                inputs[key] = fetch(SOURCES[key]["url"])
    out = a.out or (BUNDLED if a.bundled else os.path.join(os.environ.get("DATA_DIR") or os.path.join(ROOT, "data"),
                                                            "directory", "providers.json.gz"))
    doc = build(inputs, today=CURATED_DATE if a.bundled else None)   # the bundled file changes only with its entries
    size, raw = write(doc, out)
    for s in doc["sources"]:
        n = f", {s['entries']} entries" if "entries" in s else ""
        print(f"  {s['id']}: {'included' + n if s['included'] else 'not included'}", file=sys.stderr)
    print(f"Wrote {len(doc['entries'])} entries to {out}: {size / 1e6:.1f} MB ({raw / 1e6:.1f} MB as JSON).", file=sys.stderr)


if __name__ == "__main__":
    main()
