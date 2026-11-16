#!/usr/bin/env python3
"""Check the install templates in deploy/ against compose.release.yaml (run in CI by check.yml).

  python3 deploy/check.py [--truenas-lib DIR]

Every template must parse, use the released image, and run the same services with the same mounts and ports as
compose.release.yaml: TrueNAS and CasaOS as the same four services, Unraid as the all-in-one container
(docker/allinone.py), which has every folder those services mount and both published ports.

--truenas-lib DIR       also render the TrueNAS app with TrueNAS's app library (library/<version> of
                        github.com/truenas/apps), as their CI does, and compare the rendered compose file

The CasaOS AppStore's own validator (.github/actions/validate-compose in github.com/IceWhaleTech/CasaOS-AppStore)
checks the top-level name, x-casaos.id and `docker compose config -q`; the first two are checked here too, and
check.yml runs the third.

Needs PyYAML (and Jinja2 for --truenas-lib). Exits non-zero, listing what failed, if anything did.
"""
import argparse, importlib.util, json, os, re, sys, types
import xml.etree.ElementTree as ET

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMAGE = "ghcr.io/sanguity/sanguity"
SERVICES = {"setup", "app", "web", "receiver"}
# Where the TrueNAS app can't match compose.release.yaml exactly: its library mounts a whole dataset, so the app's one
# writable /watch stands for both of compose's mounts, public/ (read-only there) and keys/ (#46, written by the app)
TRUENAS_SAME = {("app", "/watch"): ("/watch/public", "/watch/keys")}
failed, passed = [], []


def check(name, ok, detail=""):
    (passed if ok else failed).append(f"{name}{': ' + str(detail) if detail != '' and not ok else ''}")
    return ok


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def split_volume(volume):
    """(source, target, read-only) of a compose volume, short or long syntax; the source may be ${VAR:-default}."""
    if isinstance(volume, dict):
        return volume.get("source", ""), volume["target"], bool(volume.get("read_only"))
    m = re.fullmatch(r"((?:\$\{[^}]*\}|\$\w+|[^:$])+):([^:]+)(?::(ro|rw))?", volume)
    if not m:
        raise ValueError(f"can't read the volume {volume!r}")
    return m.group(1), m.group(2), m.group(3) == "ro"


def target(volume):
    return split_volume(volume)[1]


def read_only(volume):
    return split_volume(volume)[2]


def source(volume):
    return split_volume(volume)[0]


def container_ports(svc):
    out = set()
    for p in svc.get("ports", []):
        out.add(int(p["target"]) if isinstance(p, dict) else int(str(p).split(":")[-1].split("/")[0]))
    return out


def shape(compose):
    """{service: {"mounts": {target: read_only}, "ports": {container ports}, "command": [...]}}"""
    out = {}
    for name, svc in compose["services"].items():
        mounts = {target(v): read_only(v) for v in svc.get("volumes", [])}
        mounts.update({t.split(":")[0]: False for t in (svc.get("tmpfs") or {})})
        cmd = svc.get("command")
        out[name] = {"mounts": mounts, "ports": container_ports(svc), "command": cmd, "user": svc.get("user"),
                     "read_only": svc.get("read_only", False)}
    return out


def compare(label, want, got, same=None):
    same = same or {}
    check(f"{label}: the same services as compose.release.yaml", set(got) == set(want), sorted(got))
    for name in sorted(set(want) & set(got)):
        w, g = want[name], got[name]
        gm = {}
        for t, ro in g["mounts"].items():
            stands_for = same.get((name, t), t)
            if isinstance(stands_for, tuple):   # one mount for several of compose's: each as compose has it
                gm.update({s: w["mounts"].get(s, ro) for s in stands_for})
            else:
                gm[stands_for] = ro
        check(f"{label}: {name} mounts the same folders", set(gm) == set(w["mounts"]),
              f"missing {sorted(set(w['mounts']) - set(gm))}, extra {sorted(set(gm) - set(w['mounts']))}")
        wrong = sorted(t for t in set(gm) & set(w["mounts"]) if gm[t] != w["mounts"][t])
        check(f"{label}: {name}'s read-only mounts are read-only (and the rest writable)", not wrong, wrong)
        check(f"{label}: {name} publishes the same container ports", g["ports"] == w["ports"], sorted(g["ports"]))
        check(f"{label}: {name} runs the same command", g["command"] == w["command"], g["command"])
        check(f"{label}: {name} has a read-only root filesystem", bool(g["read_only"]) == bool(w["read_only"]))


def image_ok(label, image):
    check(f"{label}: uses the released image {IMAGE}", str(image).split("@")[0].rsplit(":", 1)[0] == IMAGE, image)


# ---------------------------------------------------------------- compose.release.yaml

def release_shape():
    compose = load_yaml(os.path.join(ROOT, "compose.release.yaml"))
    check("compose.release.yaml: four services", set(compose["services"]) == SERVICES, sorted(compose["services"]))
    for name, svc in compose["services"].items():
        check(f"compose.release.yaml: {name} uses the released image",
              svc.get("image", "").startswith("${SANGUITY_IMAGE:-" + IMAGE + "}:"), svc.get("image"))
        check(f"compose.release.yaml: {name} isn't built from the checkout", "build" not in svc)
        for v in svc.get("volumes", []):
            src = source(v)
            check(f"compose.release.yaml: {name} mounts nothing from a checkout ({src})",
                  src.startswith("${") and src.endswith(("}", "}/public", "}/keys")), src)
    s = shape(compose)
    key = "/run/secrets/sanguity"
    sources = {target(v): source(v) for svc in compose["services"].values() for v in svc.get("volumes", [])}
    check("compose.release.yaml: the key folder is a folder of its own (AUTH_DIR), not inside DATA_DIR",
          sources.get(key, "").startswith("${AUTH_DIR"), sources.get(key))
    check("compose.release.yaml: only setup can write the key folder",
          all(m["mounts"].get(key, True) is (n != "setup") or key not in m["mounts"] for n, m in s.items()))
    return compose, s


# ---------------------------------------------------------------- TrueNAS

def render_truenas(app_dir, lib_dir, values_file):
    """Render templates/docker-compose.yaml the way TrueNAS's renderer does: Jinja2 with the "do" extension and
    the app library as ix_lib.base, on ix_values.yaml merged with a test values file."""
    import jinja2
    for stub in ("bcrypt", "docker"):   # used only by helpers this app doesn't call
        sys.modules.setdefault(stub, types.ModuleType(stub))
    os.environ.setdefault("FAKE_ENV", "1")
    spec = importlib.util.spec_from_file_location("base", os.path.join(lib_dir, "__init__.py"),
                                                  submodule_search_locations=[lib_dir])
    base = importlib.util.module_from_spec(spec)
    sys.modules["base"] = base
    spec.loader.exec_module(base)
    for f in sorted(os.listdir(lib_dir)):
        if f.endswith(".py") and f != "__init__.py":
            setattr(base, f[:-3], importlib.import_module("base." + f[:-3]))
    values = load_yaml(os.path.join(app_dir, "ix_values.yaml"))
    values.update(load_yaml(values_file))
    values.setdefault("ix_certificates", {})
    values.setdefault("ix_context", {"app_name": "sanguity", "is_upgrade": False, "is_install": True})
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(app_dir, "templates")),
                             extensions=["jinja2.ext.do"])
    out = env.get_template("docker-compose.yaml").render(values=values, ix_lib=types.SimpleNamespace(base=base))
    return json.loads(out)


def truenas(want, lib_dir):
    app_dir = os.path.join(HERE, "truenas", "sanguity")
    files = {}
    for root, _, names in os.walk(app_dir):
        for n in names:
            p = os.path.join(root, n)
            if n.endswith(".yaml") and "templates" + os.sep + "docker-compose.yaml" not in p:
                try:
                    files[os.path.relpath(p, app_dir)] = load_yaml(p)
                    check(f"truenas: {os.path.relpath(p, app_dir)} parses", True)
                except yaml.YAMLError as e:
                    check(f"truenas: {os.path.relpath(p, app_dir)} parses", False, e)
    app, ixv = files.get("app.yaml", {}), files.get("ix_values.yaml", {})
    for k in ("name", "title", "train", "version", "app_version", "lib_version", "description", "home", "icon",
              "categories", "maintainers", "run_as_context", "sources", "annotations"):
        check(f"truenas: app.yaml has {k}", k in app)
    check("truenas: app.yaml's name is sanguity", app.get("name") == "sanguity")
    image_ok("truenas", f"{ixv['images']['image']['repository']}:{ixv['images']['image']['tag']}")
    check("truenas: the image tag is app_version", str(ixv["images"]["image"]["tag"]).split("@")[0] == str(app.get("app_version")))
    qvars = {q["variable"] for q in files["questions.yaml"]["questions"]}
    for t in os.listdir(os.path.join(app_dir, "templates", "test_values")):
        tv = files[os.path.join("templates", "test_values", t)]
        extra = set(tv) - qvars - {"ix_volumes"}
        check(f"truenas: {t} sets only what questions.yaml asks", not extra, sorted(extra))
    storage = next(q for q in files["questions.yaml"]["questions"] if q["variable"] == "storage")
    have = {a["variable"] for a in storage["schema"]["attrs"]}
    need = set(re.findall(r"values\.storage\.(\w+)", open(os.path.join(app_dir, "templates", "docker-compose.yaml"), encoding="utf-8").read()))
    check("truenas: questions.yaml asks for every storage the template mounts", need <= have, sorted(need - have))
    if not lib_dir:
        return
    lib = os.path.join(lib_dir, str(app["lib_version"])) if os.path.isdir(os.path.join(lib_dir, str(app["lib_version"]))) else lib_dir
    for t in sorted(os.listdir(os.path.join(app_dir, "templates", "test_values"))):
        try:
            rendered = render_truenas(app_dir, lib, os.path.join(app_dir, "templates", "test_values", t))
        except Exception as e:   # report it with the rest instead of a traceback
            check(f"truenas: renders with {t} (library {app['lib_version']})", False, f"{type(e).__name__}: {e}")
            continue
        check(f"truenas: renders with {t} (library {app['lib_version']})", True)
        perms = rendered["services"].pop("permissions", None)
        check("truenas: a permissions container hands the folders to the run-as user first", perms is not None)
        for name, svc in rendered["services"].items():
            image_ok(f"truenas: {name}", svc["image"])
            check(f"truenas: {name} runs as the run-as user", svc.get("user") == "568:568", svc.get("user"))
        compare(f"truenas ({t})", want, shape(rendered), TRUENAS_SAME)
        env = rendered["services"]["receiver"]["environment"]
        check("truenas: the receiver gets the time zone", env.get("TZ_DEFAULT") == "Etc/UTC", env.get("TZ_DEFAULT"))
        ports = {p["published"] for s in rendered["services"].values() for p in s.get("ports", [])}
        check("truenas: published on 8730 and 8731", ports == {8730, 8731}, ports)


# ---------------------------------------------------------------- Unraid

def unraid(want):
    path = os.path.join(HERE, "unraid", "sanguity.xml")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        check("unraid: sanguity.xml parses", False, e)
        return
    check("unraid: sanguity.xml parses", True)
    check("unraid: a version 2 Container template", root.tag == "Container" and root.get("version") == "2")
    for k in ("Name", "Repository", "Registry", "Network", "Support", "Project", "Overview", "Category", "WebUI",
              "TemplateURL", "Icon"):
        check(f"unraid: has {k}", (root.findtext(k) or "").strip() != "")
    image_ok("unraid", root.findtext("Repository"))
    check("unraid: runs the image's default command (the all-in-one container)", not (root.findtext("PostArgs") or "").strip())
    configs = root.findall("Config")
    for c in configs:
        check(f"unraid: Config {c.get('Name')} has a Type, Target and Default",
              c.get("Type") in ("Port", "Path", "Variable", "Label", "Device") and c.get("Target") is not None and c.get("Default") is not None)
        check(f"unraid: Config {c.get('Name')}'s value is its default", (c.text or "") == c.get("Default"), c.text)
    paths = {c.get("Target"): c.get("Mode") for c in configs if c.get("Type") == "Path"}
    # every folder the services mount; a folder inside another (the app's /watch/public) is covered by it
    mounts = {}
    for s in want.values():
        for t, ro in s["mounts"].items():
            if t != "/tmp":
                mounts[t] = mounts.get(t, True) and ro
    needed = {t: ro for t, ro in mounts.items() if not any(t.startswith(o + "/") for o in mounts)}
    check("unraid: mounts every folder the services do", set(paths) == set(needed),
          f"missing {sorted(set(needed) - set(paths))}, extra {sorted(set(paths) - set(needed))}")
    for t, ro in needed.items():
        if t in paths:
            check(f"unraid: {t} is {'read-only' if ro else 'writable'}", (paths[t] == "ro") == ro, paths[t])
    ports = {int(c.get("Target")): int(c.get("Default")) for c in configs if c.get("Type") == "Port"}
    check("unraid: publishes the web app on 8730 and the receiver on 8731", ports == {8080: 8730, 8081: 8731}, ports)
    variables = {c.get("Target") for c in configs if c.get("Type") == "Variable"}
    check("unraid: sets PUID, PGID and TZ", {"PUID", "PGID", "TZ"} <= variables, sorted(variables))
    key = next((c for c in configs if c.get("Target") == "/run/secrets/sanguity"), None)
    data = next((c for c in configs if c.get("Target") == "/data"), None)
    if key is not None and data is not None:
        k, d = key.get("Default").rstrip("/"), data.get("Default").rstrip("/")
        check("unraid: the key folder isn't inside the data folder (or the reverse)",
              not (k + "/").startswith(d + "/") and not (d + "/").startswith(k + "/"), (k, d))
    check("unraid: WebUI opens the web port", "[PORT:8080]" in (root.findtext("WebUI") or ""))


# ---------------------------------------------------------------- CasaOS

CASAOS_CATEGORIES = {"Media", "Productivity", "Home", "Networking", "AI", "Finance", "Social", "Developer", "Others"}


def casaos(want):
    app_dir = os.path.join(HERE, "casaos", "Sanguity")
    path = os.path.join(app_dir, "docker-compose.yml")
    try:
        compose = load_yaml(path)
    except yaml.YAMLError as e:
        check("casaos: docker-compose.yml parses", False, e)
        return
    check("casaos: docker-compose.yml parses", True)
    x = compose.get("x-casaos", {})
    check("casaos: the top-level name is lowercase letters, digits, _ or -",
          re.fullmatch(r"[a-z0-9_-]+", str(compose.get("name", ""))) is not None, compose.get("name"))
    for k in ("id", "main", "index", "port_map", "icon", "title", "tagline", "description", "author", "developer",
              "category", "architectures", "version"):
        check(f"casaos: x-casaos has {k}", k in x)
    check("casaos: id is reverse-domain", re.fullmatch(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)+", str(x.get("id", ""))) is not None)
    check("casaos: main is the web service", x.get("main") == "web")
    check("casaos: port_map is a string, the web port", x.get("port_map") == "8730", repr(x.get("port_map")))
    check("casaos: title, tagline and description have en_US",
          all(isinstance(x.get(k), dict) and x[k].get("en_US") for k in ("title", "tagline", "description")))
    check("casaos: a store category", x.get("category") in CASAOS_CATEGORIES, x.get("category"))
    check("casaos: the image's architectures", set(x.get("architectures", [])) == {"amd64", "arm64", "arm"})
    for name, svc in compose["services"].items():
        image_ok(f"casaos: {name}", svc.get("image"))
        check(f"casaos: {name} has no legacy per-service x-casaos", "x-casaos" not in svc)
        check(f"casaos: {name} runs as PUID:PGID", svc.get("user") in ("$PUID:$PGID", "0:0"), svc.get("user"))
        check(f"casaos: {name} has a fixed container name", bool(svc.get("container_name")))
    compare("casaos", want, shape(compose))
    ports = {str(p["published"]) if isinstance(p, dict) else str(p).split(":")[0]
             for s in compose["services"].values() for p in s.get("ports", [])}
    check("casaos: published on 8730 and 8731", ports == {"8730", "8731"}, ports)
    check("casaos: the receiver gets the time zone",
          compose["services"]["receiver"].get("environment", {}).get("TZ_DEFAULT") == "$TZ")
    vols = {target(v): source(v) for s in compose["services"].values() for v in s.get("volumes", [])}
    key, data = vols.get("/run/secrets/sanguity", ""), vols.get("/data", "")
    check("casaos: the key folder isn't inside the data folder", key and data and not key.startswith(data + "/"), (key, data))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--truenas-lib")
    a = ap.parse_args()
    try:
        _, want = release_shape()
        truenas(want, a.truenas_lib)
        unraid(want)
        casaos(want)
    except Exception as e:   # report what was caught so far instead of a bare traceback
        failed.append(f"stopped: {type(e).__name__}: {e}")
    for x in passed:
        print("ok    ", x)
    for x in failed:
        print("FAIL  ", x)
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
