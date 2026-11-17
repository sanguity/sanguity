"""Validate FHIR R4 JSON against HL7's official JSON schema (#205): the subset of it in
tests/fixtures/fhir-r4/fhir.schema.subset.json (tools/fhir_schema_subset.py says how it's made), checked with the
handful of JSON Schema (draft 6) keywords that schema uses: $ref, oneOf (the resource lists, picked by resourceType as
its discriminator says), type, properties, additionalProperties, required, items, enum, const and pattern. Standard
library only, so the test needs no validator package.

  errors(resource_or_bundle) -> ["<path>: <what's wrong>", ...], empty when it's valid
"""
import functools, json, os, re

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fhir-r4", "fhir.schema.subset.json")
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


@functools.lru_cache(maxsize=None)
def schema():
    with open(SCHEMA, encoding="utf-8") as f:
        return json.load(f)


@functools.lru_cache(maxsize=None)
def _pattern(p):
    return re.compile(p)


def _resource(x, defs, path, out):
    """A resource where the schema has its list of resources (ResourceList, the root): checked against its type's
    definition, as the schema's discriminator says."""
    rt = x.get("resourceType") if isinstance(x, dict) else None
    if rt not in defs or not defs[rt].get("properties", {}).get("resourceType"):
        out.append(f"{path}: a resource of a type the schema (as kept) doesn't list: {rt!r}")
        return
    _check(x, defs[rt], defs, f"{path}<{rt}>", out)


def _check(x, s, defs, path, out):
    if "$ref" in s:
        name = s["$ref"].rsplit("/", 1)[-1]
        if name == "ResourceList":
            return _resource(x, defs, path, out)
        return _check(x, defs[name], defs, path, out)
    if "oneOf" in s:
        return _resource(x, defs, path, out)
    t = s.get("type")
    if t == "number":
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return out.append(f"{path}: should be a number, not {x!r}"[:200])
    elif t == "integer":
        if isinstance(x, bool) or not isinstance(x, int):
            return out.append(f"{path}: should be an integer, not {x!r}"[:200])
    elif t and not isinstance(x, TYPES[t]):
        return out.append(f"{path}: should be {t}, not {type(x).__name__} {x!r}"[:200])
    if "const" in s and x != s["const"]:
        out.append(f"{path}: should be {s['const']!r}, not {x!r}")
    if "enum" in s and x not in s["enum"]:
        out.append(f"{path}: {x!r} isn't one of {s['enum']}"[:200])
    if "pattern" in s and isinstance(x, str) and not _pattern(s["pattern"]).search(x):
        out.append(f"{path}: {x!r} doesn't match {s['pattern']}"[:240])
    if isinstance(x, dict):
        props = s.get("properties") or {}
        for k in s.get("required") or []:
            if k not in x:
                out.append(f"{path}: {k} is required")
        for k, v in x.items():
            if k in props:
                _check(v, props[k], defs, f"{path}.{k}", out)
            elif s.get("additionalProperties") is False:
                out.append(f"{path}: {k} isn't an element of it")
    if isinstance(x, list) and "items" in s:
        for i, v in enumerate(x):
            _check(v, s["items"], defs, f"{path}[{i}]", out)
    return out


def errors(x):
    s, out = schema(), []
    _check(x, {"oneOf": s["oneOf"]}, s["definitions"], "$", out)
    return out
