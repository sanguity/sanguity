#!/usr/bin/env python3
"""Check the install templates in deploy/ against compose.release.yaml (run in CI by check.yml).

  python3 deploy/check.py [--truenas-lib DIR]

Every template must parse, use the released image, and run the same services with the same mounts and ports as
compose.release.yaml: TrueNAS and CasaOS as the same four services, Unraid as the all-in-one container
(docker/allinone.py), which has every folder those services mount and both published ports.

(#1158) The settings file is the source of truth for every server setting, as Sonarr's and Radarr's: the templates
pass only the container's own variables (server/config.py's CONTAINER, each with why, plus TZ, the containers' clock),
never a setting the page owns nor one an install may opt to manage in its environment (MANAGED: the sign-in provider,
the notification token). The compose files hand .env to the app as it is (env_file, not required), so a setting still
there is taken in once on the first start of a version that keeps them in the settings file, and a later change to it
is warned about. .env.example sets none of them and shows the opt-in ones only commented out; the all-in-one container
and the released image set none.

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
# writable /watch stands for both of compose's mounts, the whole folder (read-only there: its summaries, and its data
# for backups, #227) and keys/ (#46, written by the app)
TRUENAS_SAME = {("app", "/watch"): ("/watch", "/watch/keys")}
failed, passed = [], []


def _config():
    """server/config.py (it needs nothing but the standard library)."""
    spec = importlib.util.spec_from_file_location("sanguity_config", os.path.join(ROOT, "server", "config.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CONFIG = _config()
SETTINGS = set(CONFIG.VARS)
with open(os.path.join(ROOT, "server", "server_settings.py"), encoding="utf-8") as _f:   # (#1121: read as text, as below)
    RESTART = set(re.findall(r'^define\("(\w+)"', _f.read(), re.M))
# (#1158) what a template may pass: the container's own, and TZ (each container's clock; the receiver's time zone is
# the page's, taken in from it once), and what Python itself reads
TEMPLATE_ENV = set(CONFIG.CONTAINER) | {"TZ", "PYTHONDONTWRITEBYTECODE"}
MANAGED = set(CONFIG.MANAGED) | {n + "_FILE" for n in CONFIG.MANAGED}
NOT_PASSED = ((SETTINGS - set(CONFIG.CONTAINER)) | RESTART | MANAGED) - {"TZ"}
# (#1158) for this release, TrueNAS's and Unraid's old fields stay, as legacy, passed only when filled in: an upgraded
# app carries them into the one-time import (copied into Settings on first start; cleared afterwards)
LEGACY = {"PUBLIC_ORIGINS", "COOKIE_SECURE"}


def _page_owned():
    """(#1120) The settings the page owns: each install setting server/settings.py defines with env="NAME" (read as text:
    the registry imports the server's encryption, which this check doesn't need)."""
    with open(os.path.join(ROOT, "server", "settings.py"), encoding="utf-8") as f:
        return set(re.findall(r'(?:^\s*|,\s*)env="(\w+)"', f.read(), re.M))   # a define()'s argument, not its docs


PAGE_OWNED = _page_owned()


def env_of(svc):
    env = svc.get("environment") or {}
    return dict(e.split("=", 1) if "=" in e else (e, "") for e in env) if isinstance(env, list) else env


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


def container_only(label, services, extra=()):
    """(#1158) Every service gets only the container's own variables (and extra: what an admin added), and the app and
    the receiver the same clock (TZ)."""
    for name, svc in services.items():
        env = env_of(svc)
        if extra is None:   # (TrueNAS rendered: its library adds variables of its own) none of the server's settings
            check(f"{label}: {name} gets none of the server's settings but the legacy fields (#1158)", not set(env) & NOT_PASSED - LEGACY,
                  sorted(set(env) & NOT_PASSED - LEGACY))
            continue
        check(f"{label}: {name} gets only the container's own variables (#1158)", set(env) <= TEMPLATE_ENV | set(extra),
              sorted(set(env) - TEMPLATE_ENV - set(extra)))
    app, rx = env_of(services["app"]), env_of(services["receiver"])
    check(f"{label}: the app and the receiver keep the same clock (TZ)", app.get("TZ") == rx.get("TZ"), (app.get("TZ"), rx.get("TZ")))


# ---------------------------------------------------------------- compose.release.yaml

def compose_settings(name):
    """compose.yaml or compose.release.yaml (#1158): the container's own variables only; the app is handed .env as it is
    (env_file, not required), so a setting still in .env is taken in once and a later change to it warned about; the
    receiver's published port and the way back in only as .env sets them."""
    compose = load_yaml(os.path.join(ROOT, name))
    app = compose["services"]["app"]
    container_only(name, compose["services"])
    files = app.get("env_file")
    check(f"{name}: the app is handed .env as it is, not required (#1158)",
          files == [{"path": ".env", "required": False}], files)
    env = env_of(app)
    for k in ("RECEIVER_PORT", "SANGUITY_SAVED_SETTINGS"):
        check(f"{name}: the app gets {k} only as .env sets it", env.get(k) == "${" + k + ":-}", env.get(k))


def env_example():
    """(#1158) .env.example sets none of the server's settings nor of the ones an install may manage, and shows the latter
    only commented out ("# NAME=..."); it names no page setting at all (they're changed on the page)."""
    with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    set_ = {m.group(1) for m in (re.match(r"\s*(\w+)\s*=", l) for l in lines) if m}
    examples = {m.group(1) for m in (re.match(r"#\s?(\w+)=", l) for l in lines) if m}
    check(".env.example: sets only the container's own (#1158)", set_ <= TEMPLATE_ENV | {"WEB_PORT", "RECORDS_DIR", "DATA_DIR",
          "BACKUP_DIR", "WATCH_DIR", "AUTH_DIR", "CONFIG_DIR", "COMPOSE_PROJECT_NAME", "SANGUITY_VERSION"}, sorted(set_ - TEMPLATE_ENV))
    page = (SETTINGS - set(CONFIG.CONTAINER)) | RESTART
    check(".env.example: no page setting, not even commented out (#1158)", not (page - {"TZ"}) & (set_ | examples),
          sorted((page - {"TZ"}) & (set_ | examples)))
    check(".env.example: each setting an install may manage, commented out (#1158)",
          set(CONFIG.MANAGED) <= examples and not MANAGED & set_, sorted(set(CONFIG.MANAGED) - examples))


def image_and_allinone():
    """Neither the all-in-one container (docker/allinone.py, beyond its docstring) nor the released image (an ENV of
    docker/app.Dockerfile) sets a server setting, or one an install may manage (#1120, #1158)."""
    import ast
    tree = ast.parse(open(os.path.join(ROOT, "docker", "allinone.py"), encoding="utf-8").read())
    doc = ast.get_docstring(tree, clean=False)
    named = sorted({n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and n.value != doc and n.value in NOT_PASSED})
    check("the all-in-one container sets no server setting (#1120, #1158)", not named, named)
    docker = open(os.path.join(ROOT, "docker", "app.Dockerfile"), encoding="utf-8").read()
    env = " ".join(re.findall(r"^ENV\s+((?:.*\\\n)*.*)$", docker, re.M))
    named = sorted(k for k in NOT_PASSED if re.search(r"\b" + k + r"\s*=", env))
    check("the released image sets no server setting (#1120, #1158)", not named, named)


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
    template = open(os.path.join(app_dir, "templates", "docker-compose.yaml"), encoding="utf-8").read()
    need = set(re.findall(r"values\.storage\.(\w+)", template))
    check("truenas: questions.yaml asks for every storage the template mounts", need <= have, sorted(need - have))
    given = sorted(set(re.findall(r'add_env\("(\w+)"', template)) - TEMPLATE_ENV - LEGACY)   # (#1158) the container's own only
    check("truenas: the template passes only the container's own variables, and the legacy fields (#1158)", not given, given)
    depth, plain = 0, []   # a legacy field's add_env outside any {% if %}: passed with its default
    for line in template.splitlines():
        depth += len(re.findall(r"{%-?\s*(?:if|for)\b", line)) - len(re.findall(r"{%-?\s*end(?:if|for)\b", line))
        plain += [k for k in re.findall(r'add_env\("(\w+)"', line) if k in LEGACY and depth <= 0]
    check("truenas: a legacy field is passed only when filled in (each add_env inside an if)", not plain, plain)
    sanguity = next(q for q in files["questions.yaml"]["questions"] if q["variable"] == "sanguity")
    defaults = {a["variable"]: a["schema"].get("default") for a in sanguity["schema"]["attrs"]}
    legacy = {a["variable"]: a for a in sanguity["schema"]["attrs"] if a["variable"] in ("public_origins", "cookie_secure")}
    check("truenas: the legacy fields say so, and are empty (auto) by default (#1158)",
          len(legacy) == 2 and all("Legacy:" in a.get("description", "") for a in legacy.values())
          and defaults.get("public_origins") == "" and defaults.get("cookie_secure") == "auto", sorted(legacy))
    check("truenas: Additional Environment Variables is empty by default", defaults.get("additional_envs") == [],
          defaults.get("additional_envs"))
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
            if name != "setup":   # (ix_values.yaml's setup_container_name; it runs once)
                check(f"truenas ({t}): {name} restarts unless stopped (#1121)", svc.get("restart") == "unless-stopped", svc.get("restart"))
        compare(f"truenas ({t})", want, shape(rendered), TRUENAS_SAME)
        tv = load_yaml(os.path.join(app_dir, "templates", "test_values", t))
        app_env = env_of(rendered["services"]["app"])
        container_only(f"truenas ({t})", rendered["services"], None)
        ports = {p["published"] for s in rendered["services"].values() for p in s.get("ports", [])}
        want_ports = {tv["network"]["web_port"]["port_number"], tv["network"]["receiver_port"]["port_number"]}
        check(f"truenas ({t}): published on its web and receiver ports", ports == want_ports, (ports, want_ports))
        rx = {p["published"] for p in rendered["services"]["receiver"].get("ports", [])}
        check(f"truenas ({t}): the app gets the receiver's published port, for the phone upload address (#1007)",
              {int(app_env.get("RECEIVER_PORT", 0))} == rx, (app_env.get("RECEIVER_PORT"), rx))


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
    # every folder the services mount; a folder inside another (the app's /watch/keys) is covered by it
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
    rp = next((c for c in configs if c.get("Type") == "Variable" and c.get("Target") == "RECEIVER_PORT"), None)
    check("unraid: the app gets the receiver's published port, the same default, for the phone upload address (#1007)",
          rp is not None and rp.get("Default") == str(ports.get(8081)) and rp.get("Required") == "true", rp is not None and rp.attrib)
    variables = {c.get("Target") for c in configs if c.get("Type") == "Variable"}
    check("unraid: sets PUID, PGID and TZ", {"PUID", "PGID", "TZ"} <= variables, sorted(variables))
    check("unraid: sets only the container's own variables, and the legacy fields (#1158)", variables <= TEMPLATE_ENV | LEGACY,
          sorted(variables - TEMPLATE_ENV - LEGACY))
    filled = sorted(c.get("Target") for c in configs if c.get("Type") == "Variable" and c.get("Target") in LEGACY
                    and (c.get("Default") or (c.text or "") or "Legacy:" not in (c.get("Description") or "")))
    check("unraid: the legacy fields are empty and say they're legacy (#1158)", not filled, filled)
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
    rx = {str(p["published"]) if isinstance(p, dict) else str(p).split(":")[0] for p in compose["services"]["receiver"].get("ports", [])}
    got = str(env_of(compose["services"]["app"]).get("RECEIVER_PORT"))
    check("casaos: the app gets the receiver's published port, for the phone upload address (#1007)", {got} == rx, (got, rx))
    check("casaos: the receiver keeps the containers' clock", env_of(compose["services"]["receiver"]).get("TZ") == "$TZ")
    container_only("casaos", compose["services"])
    vols = {target(v): source(v) for s in compose["services"].values() for v in s.get("volumes", [])}
    key, data = vols.get("/run/secrets/sanguity", ""), vols.get("/data", "")
    check("casaos: the key folder isn't inside the data folder", key and data and not key.startswith(data + "/"), (key, data))


# ---------------------------------------------------------------- the deployment's kind (#1004)

def _kinds():
    """The kinds Settings → General → About names: the keys of server/general.py's KINDS (read as text: it imports the
    server's encryption, which this check doesn't need)."""
    with open(os.path.join(ROOT, "server", "general.py"), encoding="utf-8") as f:
        text = f.read()
    return set(re.findall(r'^    "(\w+)": \{"name"', text, re.M))


def deployments():
    """Each way to deploy sets SANGUITY_DEPLOYMENT for the app, a kind General → About names (#1004); the released
    image also sets SANGUITY_IMAGE_VERSION, its version (#1158: never SANGUITY_VERSION, which .env sets to pick the tag)."""
    kinds = _kinds()
    for name, kind in (("compose.yaml", "compose"), ("compose.release.yaml", "release"),
                       (os.path.join("deploy", "casaos", "Sanguity", "docker-compose.yml"), "casaos")):
        got = env_of(load_yaml(os.path.join(ROOT, name))["services"]["app"]).get("SANGUITY_DEPLOYMENT")
        check(f"{name}: the app is told it's deployed as {kind}, a kind About names", got == kind and kind in kinds, got)
    template = open(os.path.join(HERE, "truenas", "sanguity", "templates", "docker-compose.yaml"), encoding="utf-8").read()
    check("truenas: the app is told it's deployed as truenas, a kind About names",
          'app.environment.add_env("SANGUITY_DEPLOYMENT", "truenas")' in template and "truenas" in kinds)
    root = ET.parse(os.path.join(HERE, "unraid", "sanguity.xml")).getroot()
    c = next((c for c in root.findall("Config") if c.get("Target") == "SANGUITY_DEPLOYMENT"), None)
    check("unraid: the app is told it's deployed as unraid, a kind About names",
          c is not None and c.get("Type") == "Variable" and c.get("Default") == (c.text or "") == "unraid" and "unraid" in kinds,
          c is not None and c.attrib)
    aio = open(os.path.join(ROOT, "docker", "allinone.py"), encoding="utf-8").read()
    check("the all-in-one container tells the app it's deployed as allinone, unless its template says (Unraid), a kind About names",
          '{"SANGUITY_DEPLOYMENT": "allinone", **base' in aio and "allinone" in kinds)
    docker = open(os.path.join(ROOT, "docker", "app.Dockerfile"), encoding="utf-8").read()
    check("the released image sets SANGUITY_IMAGE_VERSION, the version About shows (#1158)", "SANGUITY_IMAGE_VERSION=${VERSION}" in docker)


# ---------------------------------------------------------------- restart policies (#1121)

def restart_policies():
    """Restart now (Settings → General → Needs a restart) stops the app, and the receiver stops itself when its settings
    change: Docker's restart policy, unless-stopped, starts each again, in every template. setup runs once ("no", or
    TrueNAS's on-failure)."""
    for name in ("compose.yaml", "compose.release.yaml", os.path.join("deploy", "casaos", "Sanguity", "docker-compose.yml")):
        services = load_yaml(os.path.join(ROOT, name))["services"]
        for svc in ("app", "web", "receiver"):
            check(f"{name}: {svc} restarts unless stopped (#1121)", services[svc].get("restart") == "unless-stopped",
                  services[svc].get("restart"))
    template = open(os.path.join(HERE, "truenas", "sanguity", "templates", "docker-compose.yaml"), encoding="utf-8").read()
    policies = re.findall(r"{%\s*do\s+(\w+)\.restart\.set_policy\(\"([\w-]+)\"", template)
    # (the app library's containers restart unless stopped unless set otherwise: only setup's is)
    check("truenas: only setup has a restart policy of its own; the rest restart unless stopped (#1121)",
          all(c == "setup" or p == "unless-stopped" for c, p in policies), policies)
    root = ET.parse(os.path.join(HERE, "unraid", "sanguity.xml")).getroot()
    check("unraid: the container restarts unless stopped (ExtraParams --restart unless-stopped, #1121)",
          re.search(r"--restart[ =]unless-stopped\b", root.findtext("ExtraParams") or "") is not None, root.findtext("ExtraParams"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--truenas-lib")
    a = ap.parse_args()
    try:
        check("every setting the page owns is one of the server's settings (server/config.py's VARS)",
              bool(PAGE_OWNED) and PAGE_OWNED <= SETTINGS, sorted(PAGE_OWNED - SETTINGS))
        check("no container's own variable is a page setting or a managed one (#1158)",
              not set(CONFIG.CONTAINER) & (RESTART | MANAGED | PAGE_OWNED), sorted(set(CONFIG.CONTAINER) & (RESTART | MANAGED | PAGE_OWNED)))
        for name in ("compose.yaml", "compose.release.yaml"):
            compose_settings(name)
        env_example()
        image_and_allinone()
        _, want = release_shape()
        truenas(want, a.truenas_lib)
        unraid(want)
        casaos(want)
        deployments()
        restart_policies()
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
