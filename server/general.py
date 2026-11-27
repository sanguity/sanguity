"""Settings → Install → General's read-only parts (#998): what Backups and Logging (#1002) and About (#1004) show of
this server, said in the terms of the way it's deployed. Nothing here is a secret (the key file's path and how many
keys it holds, never a key), nothing holds health data, and nothing is contacted.

The way it's deployed: SANGUITY_DEPLOYMENT, which each of deploy/'s templates and both compose files set
(deploy/check.py checks), and the all-in-one container sets for itself (docker/allinone.py). Unset (the server started
some other way, or from an install from before #1004): "other", which names each place."""
import os

from . import config, crypto, settings

# per way: its name, where the server's settings (the environment's) are set, where the app's output is read, and what
# mounts each folder (the field, path or variable), by the folder's key in FOLDERS
KINDS = {
    "compose": {"name": "Docker Compose, built from a checkout of the source",
                "settings": ".env, beside compose.yaml, then docker compose up -d",
                "logs": ("docker compose logs app", "in the checkout's folder"),
                "mounts": {"data": ".env's DATA_DIR (./data by default)", "backups": ".env's BACKUP_DIR (./backups by default)",
                           "key": "auth/ in the checkout", "records": ".env's RECORDS_DIR (./records by default)",
                           "watch": ".env's WATCH_DIR (./watch-data by default)", "config": "the checkout's own folder",
                           "user": ".env's PUID and PGID (1000 by default)"}},
    "release": {"name": "Docker Compose, the released image (compose.release.yaml)",
                "settings": ".env, beside compose.release.yaml, then docker compose -f compose.release.yaml up -d",
                "logs": ("docker compose -f compose.release.yaml logs app", "in its folder"),
                "mounts": {"data": ".env's DATA_DIR (./data by default)", "backups": ".env's BACKUP_DIR (./backups by default)",
                           "key": ".env's AUTH_DIR (./auth by default)", "records": ".env's RECORDS_DIR (./records by default)",
                           "watch": ".env's WATCH_DIR (./watch-data by default)", "config": ".env's CONFIG_DIR (./config by default)",
                           "user": ".env's PUID and PGID (1000 by default)"}},
    "truenas": {"name": "TrueNAS app",
                "settings": "the app's Edit page in TrueNAS: Additional Environment Variables",
                "logs": (None, "TrueNAS: Apps, Sanguity, then the Logs of its app container"),
                "mounts": {"data": "Sanguity Data Storage, on the app's Edit page",
                           "backups": "Sanguity Backups Storage, on the app's Edit page",
                           "key": "Sanguity Encryption Key Storage, on the app's Edit page",
                           "records": "Sanguity Record Exports Storage, on the app's Edit page",
                           "watch": "Sanguity Watch Data Storage, on the app's Edit page",
                           "config": "Sanguity Config Storage, on the app's Edit page",
                           "user": "User ID and Group ID, on the app's Edit page (568 by default)"}},
    "unraid": {"name": "Unraid (the all-in-one container)",
               "settings": "the container's Edit page in Unraid: Add another Path, Port, Variable, Label or Device",
               "logs": (None, "Unraid: Docker, Sanguity's icon, then Logs"),
               "mounts": {"data": "Data, on the container's Edit page", "backups": "Backups, on the container's Edit page",
                          "key": "Encryption key, on the container's Edit page",
                          "records": "Record exports, on the container's Edit page",
                          "watch": "Watch data, on the container's Edit page",
                          "config": "Config files, on the container's Edit page",
                          "user": "PUID and PGID, on the container's Edit page (99 and 100 by default)"}},
    "casaos": {"name": "CasaOS app",
               "settings": "the app's Settings in CasaOS: Environment variables",
               "logs": ("docker logs sanguity-app", "on the CasaOS machine"),
               "mounts": {k: f"/DATA/AppData/sanguity/{d}, in the app's Settings (Volumes)" for k, d in
                          (("data", "data"), ("backups", "backups"), ("key", "key"), ("records", "records"),
                           ("watch", "watch"), ("config", "config"))} | {"user": "CasaOS itself (its PUID and PGID)"}},
    "allinone": {"name": "The all-in-one container, run by itself",
                 "settings": "-e NAME=value on docker run (or the environment of whatever starts it)",
                 "logs": ("docker logs", "with the container's name"),
                 "mounts": {"data": "-v …:/data", "backups": "-v …:/backups", "key": "-v …:/run/secrets/sanguity",
                            "records": "-v …:/srv/records", "watch": "-v …:/watch", "config": "-v …:/config",
                            "user": "-e PUID=… -e PGID=… (1000 by default)"}},
    "other": {"name": "Not named (started some other way)",
              "settings": "the server's environment: .env with Docker Compose, the app's environment variables elsewhere",
              "logs": (None, "the server's own output (with Docker: docker compose logs app, or docker logs)"),
              "mounts": {}},
}


def deployment():
    kind = os.environ.get("SANGUITY_DEPLOYMENT", "").strip().lower()
    return kind if kind in KINDS else "other"


def _folder(key, path, what):
    return {"key": key, "path": path, "what": what, "set_by": KINDS[deployment()]["mounts"].get(key)}


def key_count():
    """How many keys the key file holds (the first encrypts; more while one is being rotated), or None if it can't be
    read."""
    try:
        return len(crypto.keyring.load()[1])
    except crypto.KeyError_:
        return None


def backups_logging():
    """General → Backups and Logging (#1002): the backups folder and the key (where each is mounted), and where the
    app's output and its security log are read. The schedule and how many are kept are GET /api/backups' (Install →
    Backups); the days of activity kept is the install setting activity.days."""
    kind = KINDS[deployment()]
    return {"deployment": deployment(), "settings_at": kind["settings"],
            "folder": _folder("backups", config.BACKUP_DIR, "Backups"),
            "key": {**_folder("key", os.path.dirname(config.KEY_FILE), "Encryption key"), "file": config.KEY_FILE,
                    "keys": key_count()},
            "logs": {"command": kind["logs"][0], "where": kind["logs"][1]}}


# ---------------------------------------------------------------- About (#1004)
RELEASES = "https://github.com/sanguity/sanguity/releases"   # a link only: nothing here contacts it (#998, decision 5)
# the limits, read-only here for now (#998's default), each with its variable: [(group, [(variable, what, unit)])]
LIMITS = [
    ("Uploads", [("UPLOAD_MAX_FILE_MB", "One file", "MB"), ("UPLOAD_MAX_TOTAL_MB", "One upload, unpacked", "MB"),
                 ("UPLOAD_MAX_FILES", "Files in one upload", "files"),
                 ("UPLOAD_MAX_ZIP_RATIO", "A file unpacked from a zip, at most", "times its packed size"),
                 ("JOURNAL_PHOTO_MAX_MB", "A journal photo", "MB")]),
    ("Connected sources", [("SYNC_HOURS", "Time between a connection's pulls", "hours"),
                           ("SYNC_MAX_RESOURCES", "Records one pull fetches", "records"),
                           ("SYNC_MAX_ATTACHMENT_MB", "One attachment", "MB"),
                           ("CONNECT_TIMEOUT", "Wait for a vendor's answer", "seconds")]),
    ("Health Links", [("SHL_MAX_MB", "What one link brings", "MB"), ("SHL_MAX_FILES", "Files in one link", "files"),
                      ("SHL_TIMEOUT", "Wait for each request", "seconds")]),
]
CONFIG_FILES = ("sources.json", "settings.json", "corrections.json")   # read by setup (docker/setup.py) as it starts


def version():
    """{"version": SANGUITY_VERSION or None, "checkout": True for a checkout of the source (no release number)}: the
    released image sets it as it's built (docker/app.Dockerfile); "dev" is an image built without one."""
    v = os.environ.get("SANGUITY_VERSION", "").strip()
    return {"version": v if v and v != "dev" else None, "checkout": deployment() == "compose" or not v or v == "dev"}


def about():
    """General → About (#1004): the version, the database's schema version, how it's deployed, the folders (where
    they are in the container, and what mounts each), who it runs as, the configuration files, the releases page (a
    link: nothing is contacted), and the limits with their variables, read-only."""
    from . import db as dbm
    kind = deployment()
    mounts = KINDS[kind]["mounts"]
    watch = config.WATCH_STORE or os.path.dirname(config.WATCH_FILE)   # (the receiver's whole folder, else its summaries')
    limits = []
    for group, rows in LIMITS:
        if group == "Connected sources" and not settings.connected_sources():   # (#1096) none of theirs while they're off
            continue
        items = []
        for name, what, unit in rows:
            src, value = config.lookup(name)
            items.append({"name": name, "what": what, "unit": unit, "value": value, "from": src, "default": config.VARS[name].default})
        limits.append({"group": group, "limits": items})
    return {**version(), "schema": len(dbm.SCHEMA), "deployment": kind, "deployment_name": KINDS[kind]["name"],
            "settings_at": KINDS[kind]["settings"], "releases": RELEASES,
            "folders": [_folder("data", config.DATA_DIR, "Data"), _folder("backups", config.BACKUP_DIR, "Backups"),
                        _folder("key", os.path.dirname(config.KEY_FILE), "Encryption key"),
                        _folder("records", config.RECORDS_DIR, "Record exports"), _folder("watch", watch, "Phone and watch data")],
            "runs_as": {"uid": os.getuid(), "gid": os.getgid(), "set_by": mounts.get("user")},
            "config": {"folder": os.environ.get("SANGUITY_CONFIG", "").strip() or None, "files": list(CONFIG_FILES),
                       "set_by": mounts.get("config")},
            "limits": limits}
