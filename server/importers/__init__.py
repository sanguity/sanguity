"""Importers: one module per record format, and the registry that picks the one that can read an upload.

The contract (docs/importers.md has the full version, with an example):

    server/importers/<any_file_name>.py
        NAME  = "<name>"             short id: lower-case letters, digits, dashes; unique among the importers
        LABEL = "Human name"         shown to whoever is importing
        def detect(files) -> float   0.0 (not this format) to 1.0 (certain); read only what you need
        def parse(files) -> {"data": <data.json shape>, "warnings": [str, ...]}
                                     raise ValueError("plain-language reason") for input it can't read

`files` is {relative POSIX path inside the upload: absolute path on disk}, as files_in() builds it.
Events and lab series may carry "key": a stable source id, "<NAME>:..." (or "loinc:<code>" for a series), the same
every time the same source data is parsed and unique within one parse's output.

Standard library only here, so build.py can import common.py through this package.
"""
import collections, importlib, logging, os, pkgutil, re

log = logging.getLogger("sanguity.importers")

THRESHOLD = 0.2      # a best score below this means no importer matched
NOT_IMPORTERS = {"common"}   # modules in this package that hold shared code, not a format

REQUIRED_KEYS = {"events": list, "details": dict, "labs": list, "costs": list}
OPTIONAL_KEYS = {"upcoming": list, "problems": list, "milestones": list, "clinicWeights": list, "outOfPocket": dict,
                 "watch": dict}   # a daily watch summary (watch_store.py), saved apart from the records


NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def importers():
    """Every importer module in this package, sorted by NAME. A module that fails to import or lacks part of the
    contract is logged and left out, so one broken importer doesn't stop the others."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name in NOT_IMPORTERS or info.ispkg:
            continue
        try:
            found.append(importlib.import_module(f"{__name__}.{info.name}"))
        except Exception:
            log.exception("importer %s failed to load", info.name)
    return accept(found)


def accept(mods):
    """The modules that keep to the contract, sorted by NAME: NAME, LABEL, detect and parse present, NAME matching
    NAME_RE, and no NAME shared with another module (every module claiming a shared NAME is left out, since neither
    can be told apart in a key or a fixture folder). The rest are logged."""
    ok = []
    for mod in mods:
        missing = [a for a in ("NAME", "LABEL", "detect", "parse") if not hasattr(mod, a)]
        if missing:
            log.warning("importer %s left out: no %s", mod.__name__, ", ".join(missing))
        elif not isinstance(mod.NAME, str) or not NAME_RE.match(mod.NAME):
            log.warning("importer %s left out: NAME %r isn't lower-case letters, digits and dashes", mod.__name__, mod.NAME)
        else:
            ok.append(mod)
    counts = collections.Counter(m.NAME for m in ok)
    for name in sorted(n for n, c in counts.items() if c > 1):
        log.error("importers %s all have NAME %r: all left out", ", ".join(sorted(m.__name__ for m in ok if m.NAME == name)), name)
    return sorted((m for m in ok if counts[m.NAME] == 1), key=lambda m: m.NAME)


def get(name):
    """The importer whose NAME is `name`, or None."""
    return next((m for m in importers() if m.NAME == name), None)


def files_in(root):
    """The `files` dict for an upload unpacked into the folder `root`: {"a/b.json": "/abs/root/a/b.json"}."""
    root = os.path.abspath(root)
    out = {}
    for d, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(d, n)
            if os.path.isfile(p) and not os.path.islink(p):
                out[os.path.relpath(p, root).replace(os.sep, "/")] = p
    return out


def scores(files, modules=None):
    """[(score, module)] for every importer, best first; ties in NAME order. A detect() that raises scores 0."""
    out = []
    for m in importers() if modules is None else modules:
        try:
            s = float(m.detect(files) or 0.0)
        except Exception:
            log.exception("importer %s: detect failed", m.NAME)
            s = 0.0
        out.append((min(max(s, 0.0), 1.0), m))
    return sorted(out, key=lambda x: (-x[0], x[1].NAME))


def match(files, modules=None, threshold=THRESHOLD):
    """The importer for these files: {"importer": module or None, "score": float, "tied": [other modules with the
    same best score]}. With a tie the first by NAME wins, so the outcome never depends on file-system order."""
    ranked = scores(files, modules)
    if not ranked or ranked[0][0] < threshold or ranked[0][0] == 0.0:
        return {"importer": None, "score": ranked[0][0] if ranked else 0.0, "tied": []}
    best = ranked[0][0]
    return {"importer": ranked[0][1], "score": best, "tied": [m for s, m in ranked[1:] if s == best]}


def run(files, name=None, modules=None):
    """Parse an upload with the named importer, or the best match. Returns {"importer": NAME, "label": LABEL,
    "data": ..., "warnings": [...]}; raises ValueError (a message to show as it is) when nothing can read it."""
    if not files:
        raise ValueError("The upload is empty.")
    if name:
        mod = next((m for m in (importers() if modules is None else modules) if m.NAME == name), None)
        if not mod:
            raise ValueError(f"There's no importer called {name!r}.")
        tied = []
    else:
        hit = match(files, modules)
        mod, tied = hit["importer"], hit["tied"]
        if not mod:
            labels = ", ".join(m.LABEL for m in (importers() if modules is None else modules))
            raise ValueError("None of the importers recognised these files." + (f" Sanguity can read: {labels}." if labels else ""))
    out = mod.parse(files)
    problems = check(out, files)
    if problems:
        raise ValueError(f"The {mod.LABEL} importer returned records Sanguity can't load: " + "; ".join(problems[:5]))
    warnings = [str(w) for w in out.get("warnings") or []]
    if tied:
        warnings.insert(0, f"These files also look like {', '.join(m.LABEL for m in tied)}; read them as {mod.LABEL}.")
    return {"importer": mod.NAME, "label": mod.LABEL, "data": out["data"], "warnings": warnings}


def check(out, files=None):
    """Problems with what a parse() returned, as plain sentences; [] when it keeps to the contract. Checks the shape
    the app relies on, not the medical content. With `files`, document links must name a file in the upload."""
    if not isinstance(out, dict) or not isinstance(out.get("data"), dict):
        return ['parse() must return {"data": {...}, "warnings": [...]}']
    p = []
    if not isinstance(out.get("warnings", []), list):
        p.append("warnings must be a list of strings")
    data = out["data"]
    for k, typ in {**REQUIRED_KEYS, **OPTIONAL_KEYS}.items():
        if k in data and not isinstance(data[k], typ):
            p.append(f"data[{k!r}] must be a {typ.__name__}")
        elif k in REQUIRED_KEYS and k not in data:
            p.append(f"data has no {k!r}")
    if p:
        return p
    ids, event_keys, lab_keys = set(), set(), set()

    def key_ok(what, x, seen):
        """The optional stable source key: a non-empty string, unique among the events (or among the lab series)."""
        if "key" not in x:
            return
        k = x["key"]
        if not isinstance(k, str) or not k.strip():
            p.append(f"{what}: key must be a non-empty string")
        elif k in seen:
            p.append(f"{what}: key {k!r} is used twice")
        seen.add(k)

    for i, e in enumerate(data["events"]):
        if not isinstance(e, dict) or any(k not in e for k in ("id", "t", "k", "title")):
            p.append(f"event {i} needs id, t, k and title")
            continue
        if e["id"] in ids:
            p.append(f"event id {e['id']!r} is used twice")
        ids.add(e["id"])
        key_ok(f"event {e['id']!r}", e, event_keys)
    keys = {str(i) for i in ids}
    for k, d in data["details"].items():
        if str(k) not in keys:
            p.append(f"details[{k!r}] belongs to no event")
        if isinstance(d, dict) and d.get("type") == "file" and files is not None:
            url = (d.get("url") or "").split("#", 1)[0]
            if url and url not in files:
                p.append(f"document {url!r} isn't in the upload")
    for i, s in enumerate(data["labs"]):
        if not isinstance(s, dict) or "name" not in s or not isinstance(s.get("pts", []), list):
            p.append(f"lab series {i} needs a name and a pts list")
            continue
        key_ok(f"lab series {s['name']!r}", s, lab_keys)
    for k in ("problems", "upcoming"):   # entries may carry keys too, unique within their own list
        seen = set()
        for i, x in enumerate(data.get(k) or []):
            if isinstance(x, dict):
                key_ok(f"{k} {i}", x, seen)
    return p
