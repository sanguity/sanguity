#!/usr/bin/env python3
"""Make tests/fixtures/fhir-r4/fhir.schema.subset.json, what tests/test_fhir_export.py validates the FHIR export
against (#205), from HL7's official FHIR R4 JSON schema.

The official schema (https://hl7.org/fhir/R4/fhir.schema.json.zip, FHIR R4 4.0.1, CC0 like all of FHIR) is 3.4 MB:
too big to keep in the repository for one test. This keeps of it, unchanged, the definitions the export's resource
types reach (the Bundle, each resource it writes and every data type they use), drops the descriptions, and narrows
the Bundle's list of resources to those types; nothing else is edited, so a resource valid against the subset is
valid against the whole schema. The subset names the official file's SHA-256 (2230406893b4cf00...76a01 for
4.0.1's). Standard library only.

  curl -sSLo /tmp/fhir.schema.json.zip https://hl7.org/fhir/R4/fhir.schema.json.zip && unzip -d /tmp /tmp/fhir.schema.json.zip
  python3 -I tools/fhir_schema_subset.py /tmp/fhir.schema.json
"""
import hashlib, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "fixtures", "fhir-r4", "fhir.schema.subset.json")
TYPES = ["Bundle", "Patient", "Observation", "DiagnosticReport", "Encounter", "Appointment", "Condition", "AllergyIntolerance",
         "MedicationStatement", "MedicationRequest", "MedicationDispense", "Immunization", "Procedure", "ImagingStudy",
         "DocumentReference", "Communication", "ExplanationOfBenefit", "Coverage", "Practitioner", "Organization", "Location",
         "Device", "FamilyMemberHistory"]


def refs(x):
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/definitions/"):
                yield v[len("#/definitions/"):]
            else:
                yield from refs(v)
    elif isinstance(x, list):
        for v in x:
            yield from refs(v)


def strip(x, props=False):
    """The schema without its descriptions (the keyword, not an element called "description": in "properties" a key
    is an element's name)."""
    if isinstance(x, dict):
        return {k: strip(v, k == "properties" and not props) for k, v in x.items() if props or k != "description"}
    if isinstance(x, list):
        return [strip(v) for v in x]
    return x


def main(src):
    raw = open(src, "rb").read()
    full = json.loads(raw)
    defs = full["definitions"]
    keep = {"ResourceList": {"oneOf": [{"$ref": f"#/definitions/{t}"} for t in TYPES]}}
    todo = list(TYPES)
    while todo:
        name = todo.pop()
        if name in keep:
            continue
        keep[name] = strip(defs[name])
        todo += [r for r in refs(defs[name]) if r not in keep]
    out = {"$schema": full["$schema"], "id": full["id"],
           "$comment": f"A subset of HL7's FHIR R4 (4.0.1) JSON schema, fhir.schema.json (sha256 {hashlib.sha256(raw).hexdigest()}), "
                       "made by tools/fhir_schema_subset.py: the definitions the FHIR export's resource types reach, unchanged, "
                       "without their descriptions; the Bundle's ResourceList narrowed to those types. FHIR is CC0 (public domain).",
           "discriminator": {"propertyName": "resourceType", "mapping": {t: f"#/definitions/{t}" for t in TYPES}},
           "oneOf": [{"$ref": f"#/definitions/{t}"} for t in TYPES],
           "definitions": dict(sorted(keep.items()))}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), sort_keys=False)
        f.write("\n")
    print(f"wrote {OUT}: {len(keep)} definitions, {os.path.getsize(OUT):,} bytes")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
