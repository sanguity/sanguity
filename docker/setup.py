#!/usr/bin/env python3
"""Setup for the Docker install. Runs as the one-shot "setup" service before the app starts.

  docker compose up -d --build                   every start: secrets and folders, then the built data
  docker compose run --rm setup rebuild          rebuild the data after adding new exports

Data: with a sources.json next to compose.yaml, the records are built from your exports (RECORDS_DIR) into
DATA_DIR/build; the app loads that build into the first profile (and again after each rebuild). Without one, a
fictional demo dataset is generated there so there is something to look at. A settings.json next to compose.yaml
(see settings.example.json) sets that profile's dashboard preferences.

Accounts live in the app's database. The first one is created in the browser with the setup code the app prints
(docker compose logs app); after that, admins add accounts under Account. To recover a lost password:
  docker compose exec app python -m server.manage reset-password USERNAME
"""
import json, os, secrets, shutil, subprocess, sys

sys.stdout.reconfigure(line_buffering=True)   # keep our messages in order with the build/demo scripts' output

APP = os.environ.get("SANGUITY_APP", "/app")   # container paths; overridable for running outside Docker
WWW, AUTH = os.path.join(APP, "www"), os.path.join(APP, "auth")
DATA = os.environ.get("SANGUITY_DATA", "/data")
os.environ.setdefault("SANGUITY_KEY_FILE", os.path.join(AUTH, "data_key"))
os.environ.setdefault("SANGUITY_DATA", DATA)
sys.path.insert(0, APP)
from server import crypto   # noqa: E402  (the app's own encryption, so the build is stored like the app stores it)
BUILD = os.path.join(DATA, "build")
BACKUPS = os.environ.get("SANGUITY_BACKUPS", "/backups")
WATCH = os.environ.get("SANGUITY_WATCH", "/watch")
RECORDS = os.environ.get("SANGUITY_RECORDS", "/srv/records")
UID, GID = int(os.environ.get("PUID", "1000")), int(os.environ.get("PGID", "1000"))


def own(path):
    """Files written here run as root inside the container; hand them to PUID:PGID on the host."""
    try:
        os.chown(path, UID, GID)
    except (OSError, AttributeError):   # AttributeError: no chown when run outside Docker on Windows
        pass


def own_tree(top):
    own(top)
    for root, dirs, files in os.walk(top):
        for n in dirs + files:
            own(os.path.join(root, n))


def write(path, text, mode=0o600):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    own(tmp)
    os.replace(tmp, path)


def secrets_and_dirs():
    for d in (AUTH, DATA, BUILD, BACKUPS, WATCH, os.path.join(WATCH, "public")):
        os.makedirs(d, exist_ok=True)
        own(d)
    ws = os.path.join(AUTH, "watch_secret")
    if not os.path.isfile(ws):
        write(ws, secrets.token_hex(32) + "\n")
        print("Created the watch receiver secret (auth/watch_secret); the phone app signs its uploads with it.")
    if crypto.make_key_file(os.environ["SANGUITY_KEY_FILE"]):
        own(os.environ["SANGUITY_KEY_FILE"])
        print("Created the encryption key (auth/data_key). Back it up somewhere other than the data folder and its "
              "backups: without it, the records can't be read.")
    old = [n for n in ("htpasswd", "token.conf") if os.path.isfile(os.path.join(AUTH, n))]
    if old:   # the single shared login is gone; accounts are in the app's database now
        print(f"Note: auth/{' and auth/'.join(old)} from the old single login are no longer used and can be deleted.")


def is_demo(path):
    try:
        return json.loads(crypto.read_file(path)).get("source", "").startswith("Sanguity demo")
    except (OSError, ValueError, crypto.KeyError_):
        return False


def seal_build():
    """Encrypt what the build scripts wrote in plaintext (the app reads either, but stores only sealed files)."""
    for root, dirs, files in os.walk(BUILD):
        for n in files:
            p = os.path.join(root, n)
            if not n.endswith(".tmp") and crypto.needs_resealing(open(p, "rb").read(8)):
                crypto.write_file(p, crypto.read_file(p))


def run(cmd, env=None):
    r = subprocess.run(cmd, env={**os.environ, **(env or {})})
    if r.returncode:
        sys.exit(r.returncode)


def build_data():
    data, sources = os.path.join(BUILD, "data.json"), os.path.join(APP, "sources.json")
    legacy = os.path.join(WWW, "data.json")   # where data lived before accounts; nginx no longer serves it
    if os.path.isfile(sources):
        run([sys.executable, "-I", os.path.join(APP, "build.py"), "--config", sources, "--out", data + ".tmp"], {"RECORDS_ROOT": RECORDS})
        own(data + ".tmp")
        os.replace(data + ".tmp", data)
    elif not os.path.exists(data) and os.path.isfile(legacy) and not is_demo(legacy):
        shutil.copyfile(legacy, data)
        print("Moved your existing www/data.json into the app's data folder. You can delete www/data.json.")
    elif not os.path.exists(data) or is_demo(data):
        # demo: regenerated on each start so its dates stay current; real watch data is never overwritten
        real_watch = any(os.path.exists(os.path.join(WATCH, n)) for n in ("watch.db", "raw"))
        watch_args = [] if real_watch else ["--watch", os.path.join(WATCH, "public")]
        run([sys.executable, "-I", os.path.join(APP, "demo", "generate.py"), BUILD, *watch_args])
        print("No sources.json found: showing the fictional demo data. See sources.example.json to use your own records.")
    else:
        print("Built data exists and there is no sources.json; leaving it as it is.")
    # dashboard preferences for the profile linked to this build (settings.json next to compose.yaml, or the old www/ one)
    built = os.path.join(BUILD, "settings.json")
    if not is_demo(data):   # the demo writes its own
        src = next((p for p in (os.path.join(APP, "settings.json"), os.path.join(WWW, "settings.json")) if os.path.isfile(p)), None)
        if src:
            crypto.write_file(built, crypto.read_file(src))
        elif os.path.isfile(built):
            os.remove(built)   # settings.json was removed: back to the defaults
    # corrections.json next to compose.yaml: the app moves its event corrections into edits once (server/edits.py)
    built = os.path.join(BUILD, "corrections.json")
    if os.path.isfile(os.path.join(APP, "corrections.json")):
        crypto.write_file(built, crypto.read_file(os.path.join(APP, "corrections.json")))
    elif os.path.isfile(built):
        os.remove(built)
    seal_build()
    own_tree(BUILD)
    for n in os.listdir(os.path.join(WATCH, "public")):
        p = os.path.join(WATCH, "public", n)
        if n == "daily.json" and not crypto.file_is_encrypted(p):   # the demo's, written in plaintext
            crypto.write_file(p, crypto.read_file(p))
        own(p)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "password":
        sys.exit("Logins are accounts in the app now. To reset one:\n"
                 "  docker compose exec app python -m server.manage reset-password USERNAME")
    elif cmd == "new-key":   # rotation: a new first key; the app re-encrypts with it when it next starts
        crypto.add_key(os.environ["SANGUITY_KEY_FILE"])
        own(os.environ["SANGUITY_KEY_FILE"])
        print("Added a new encryption key to auth/data_key (the first line). Restart the app (docker compose restart\n"
              "app): it re-encrypts everything with the new key as it starts. After that the old key's line can go.")
    elif cmd == "rebuild":
        secrets_and_dirs()
        build_data()
    elif cmd == "start":
        secrets_and_dirs()
        build_data()
    else:
        sys.exit(__doc__)
