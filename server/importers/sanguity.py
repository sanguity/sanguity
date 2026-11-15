"""Sanguity's own records: a data.json folder (what build.py or demo/generate.py writes) or an unpacked export zip.

The smallest importer that keeps to the contract, and the one to copy when writing a new one (docs/importers.md).
The records are already in the shape Sanguity loads, so parse() only finds data.json, checks it, and points each
document link at the file in the upload. Source keys ("key" on events and lab series) pass through unchanged, so
records that first came from a FHIR or C-CDA import keep the key that importer gave them.
"""
import json, posixpath

NAME = "sanguity"
LABEL = "Sanguity data or export"

SIGNATURE = ('"events":', '"details":')


def _data_file(files):
    """The upload's data.json: at the top, or one folder down (a zip that unpacks into a folder of its own)."""
    hits = sorted((p for p in files if posixpath.basename(p) == "data.json" and p.count("/") <= 1), key=lambda p: (p.count("/"), p))
    return hits[0] if hits else None


def detect(files):
    rel = _data_file(files)
    if not rel:
        return 0.0
    with open(files[rel], "rb") as f:
        head = f.read(64 * 1024).decode("utf-8", "replace")
    if not head.lstrip().startswith("{") or SIGNATURE[0] not in head:
        return 0.0
    readme = posixpath.join(posixpath.dirname(rel), "README.txt")
    if readme in files:
        with open(files[readme], "rb") as f:
            if f.read(64).startswith(b"Sanguity export"):
                return 1.0
    return 0.9 if SIGNATURE[1] in head else 0.6


def parse(files):
    rel = _data_file(files)
    if not rel:
        raise ValueError("There's no data.json in this upload.")
    try:
        with open(files[rel], encoding="utf-8") as f:
            data = json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"{rel} isn't readable JSON ({e.__class__.__name__}).") from None
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ValueError(f"{rel} isn't Sanguity data: it has no list of events.")
    for k, empty in (("details", {}), ("labs", []), ("costs", [])):
        data.setdefault(k, empty)

    base, warnings, missing, outside = posixpath.dirname(rel), [], 0, 0
    for d in data["details"].values():
        if not isinstance(d, dict) or d.get("type") != "file" or not d.get("url"):
            continue
        url, _, frag = d["url"].partition("#")
        if "://" in url or url.startswith(("/", "records/", "api/")):
            outside += 1      # build.py's records/... links and the app's own links point outside the upload
            d["url"] = None
            continue
        inside = posixpath.normpath(posixpath.join(base, url)) if base else posixpath.normpath(url)
        if inside not in files:
            missing += 1
            d["url"] = None
            continue
        d["url"] = inside + ("#" + frag if frag else "")
    if missing:
        warnings.append(f"{missing} document{'s are' if missing != 1 else ' is'} linked in {rel} but not in the upload; "
                        "the records are kept without them.")
    if outside:
        warnings.append(f"{outside} document link{'s point' if outside != 1 else ' points'} outside the upload (to the "
                        "server's record folders); upload an export from the app to bring the documents along.")
    return {"data": data, "warnings": warnings}
