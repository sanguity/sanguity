#!/usr/bin/env python3
"""Setup for the Docker install. Runs as the one-shot "setup" service before the web app starts.

  docker compose up -d                           every start: secrets and a login if missing, then the data
  docker compose run --rm setup rebuild          rebuild www/data.json after adding new exports
  docker compose run --rm setup password         set or replace the login (asks for a username and password)

Data: with a sources.json next to compose.yaml, www/data.json is built from your exports (RECORDS_DIR).
Without one, a fictional demo dataset is generated so there is something to look at.
"""
import getpass, json, os, secrets, subprocess, sys, warnings

sys.stdout.reconfigure(line_buffering=True)   # keep our messages in order with the build/demo scripts' output

APP = os.environ.get("SANGUITY_APP", "/app")   # container paths; overridable for running outside Docker
WWW, AUTH = os.path.join(APP, "www"), os.path.join(APP, "auth")
WATCH = os.environ.get("SANGUITY_WATCH", "/watch")
RECORDS = os.environ.get("SANGUITY_RECORDS", "/srv/records")
UID, GID = int(os.environ.get("PUID", "1000")), int(os.environ.get("PGID", "1000"))


def own(path):
    """Files written here run as root inside the container; hand them to PUID:PGID on the host."""
    try:
        os.chown(path, UID, GID)
    except OSError:
        pass


def write(path, text, mode=0o600):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    own(tmp)
    os.replace(tmp, path)


def secrets_and_dirs():
    for d in (AUTH, WATCH, os.path.join(WATCH, "public")):
        os.makedirs(d, exist_ok=True)
        own(d)
    tok = os.path.join(AUTH, "token.conf")
    if not os.path.isfile(tok) or not open(tok, encoding="utf-8").read().strip():
        write(tok, f'set $ht_token "{secrets.token_hex(32)}";\n')
        print("Created the session secret (auth/token.conf).")
    ws = os.path.join(AUTH, "watch_secret")
    if not os.path.isfile(ws):
        write(ws, secrets.token_hex(32) + "\n")
        print("Created the watch receiver secret (auth/watch_secret); the phone app signs its uploads with it.")


def pw_hash(pw):
    warnings.simplefilter("ignore", DeprecationWarning)
    import crypt   # SHA-512 crypt; nginx checks it with the system crypt() (Alpine/musl supports $6$)
    return crypt.crypt(pw, crypt.mksalt(crypt.METHOD_SHA512))


def save_login(user, pw):
    write(os.path.join(AUTH, "htpasswd"), f"{user}:{pw_hash(pw)}\n", mode=0o640)


def first_login():
    if os.path.isfile(os.path.join(AUTH, "htpasswd")) and os.path.getsize(os.path.join(AUTH, "htpasswd")):
        return
    pw = secrets.token_urlsafe(12)
    save_login("admin", pw)
    bar = "=" * 64
    print(f"\n{bar}\n  First login   user: admin   password: {pw}\n"
          f"  Change it:    docker compose run --rm setup password\n{bar}\n")


def set_password():
    user = input("Username: ").strip()
    if not user or ":" in user or " " in user:
        sys.exit("Username must be non-empty, with no spaces or colons.")
    p1, p2 = getpass.getpass("Password: "), getpass.getpass("Repeat password: ")
    if not p1 or p1 != p2:
        sys.exit("Passwords are empty or don't match; nothing changed.")
    save_login(user, p1)
    print(f"Saved login for '{user}'. Existing sessions stay signed in until they log out.")


def is_demo(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("source", "").startswith("Sanguity demo")
    except (OSError, ValueError):
        return False


def run(cmd, env=None):
    r = subprocess.run(cmd, env={**os.environ, **(env or {})})
    if r.returncode:
        sys.exit(r.returncode)


def build_data():
    data, sources = os.path.join(WWW, "data.json"), os.path.join(APP, "sources.json")
    if os.path.isfile(sources):
        run([sys.executable, "-I", os.path.join(APP, "build.py"), "--config", sources, "--out", data + ".tmp"], {"RECORDS_ROOT": RECORDS})
        own(data + ".tmp")
        os.replace(data + ".tmp", data)
    elif not os.path.exists(data) or is_demo(data):
        # demo: regenerated on each start so its dates stay current; real watch data is never overwritten
        watch_args = [] if os.path.exists(os.path.join(WATCH, "watch.db")) else ["--watch", os.path.join(WATCH, "public")]
        run([sys.executable, "-I", os.path.join(APP, "demo", "generate.py"), WWW, *watch_args])
        print("No sources.json found: showing the fictional demo data. See sources.example.json to use your own records.")
    else:
        print("www/data.json exists and there is no sources.json; leaving it as it is.")
    for root, dirs, files in os.walk(WWW):
        for n in dirs + files:
            if n in ("data.json", "settings.json", "watch", "files", "daily.json", "demo-report.pdf"):
                own(os.path.join(root, n))
    for n in os.listdir(os.path.join(WATCH, "public")):
        own(os.path.join(WATCH, "public", n))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "password":
        secrets_and_dirs()
        set_password()
    elif cmd == "rebuild":
        build_data()
    elif cmd == "start":
        secrets_and_dirs()
        first_login()
        build_data()
    else:
        sys.exit(__doc__)
