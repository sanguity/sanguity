#!/usr/bin/env python3
"""Sanguity in one container: the released image's default command, for installs that run a single container
(the Unraid template in deploy/unraid/; also `docker run ghcr.io/sanguity/sanguity`).

It does what compose.release.yaml's four services do: setup (secrets, folders and the built data, as root so it
can hand every file to PUID:PGID), then the app, nginx and the watch receiver as PUID:PGID. If any of them stops,
the others are stopped too and the container exits, so Docker's restart policy starts it all again.

  Port 8080   the web app (nginx; /api/ goes to the app on 127.0.0.1:8000, which is not published)
  Port 8081   the watch receiver the phone app uploads to

Folders (mount each one; see deploy/unraid/README.md):
  /data                    the database and each profile's files
  /run/secrets/sanguity    the encryption key and the receiver's secret: keep it apart from /data and its backups
  /backups                 backups go here (Settings → Install → Backups, or python -m server.manage backup)
  /watch                   watch and scale data from the receiver
  /srv/records             record exports for sources.json (read-only; optional)
  /config                  sources.json, settings.json, corrections.json (read-only; optional)

Environment: PUID, PGID (default 1000), TZ (the watch receiver's time zone; default UTC), and the app's own
settings (COOKIE_SECURE, UPLOAD_MAX_FILE_MB... as in .env.example). It sets none of them itself: those Settings →
Install → General changes (PUBLIC_ORIGINS, SESSION_DAYS...) are the page's unless one is given here, which pins it (#1120).
"""
import os, signal, subprocess, sys, time

sys.stdout.reconfigure(line_buffering=True)
UID, GID = int(os.environ.get("PUID") or 1000), int(os.environ.get("PGID") or 1000)
AUTH = os.environ.get("SANGUITY_AUTH", "/run/secrets/sanguity")
WATCH = os.environ.get("SANGUITY_WATCH", "/watch")
NGINX_TMP = "/tmp/nginx"


def log(msg):
    print(f"[all-in-one] {msg}")


def nginx_config():
    """The image's nginx configuration, with the backend on this container's loopback instead of the app service."""
    os.makedirs(NGINX_TMP, exist_ok=True)
    site = open("/etc/sanguity/nginx.d/default.conf", encoding="utf-8").read()
    if "http://app:8000" not in site:
        sys.exit("nginx.conf no longer proxies to http://app:8000; update docker/allinone.py to match")
    with open(os.path.join(NGINX_TMP, "default.conf"), "w", encoding="utf-8") as f:
        f.write(site.replace("http://app:8000", "http://127.0.0.1:8000"))
    main = open("/etc/nginx/nginx.conf", encoding="utf-8").read()
    with open(os.path.join(NGINX_TMP, "nginx.conf"), "w", encoding="utf-8") as f:
        f.write(main.replace("/etc/sanguity/nginx.d/*.conf", os.path.join(NGINX_TMP, "default.conf")))
    for root, dirs, files in os.walk(NGINX_TMP):
        for n in [root] + [os.path.join(root, x) for x in files]:
            os.chown(n, UID, GID)


def main():
    if os.getuid() != 0:
        sys.exit("The all-in-one container starts as root (it hands its files to PUID:PGID, then runs everything "
                 "as that user). Don't set --user; set PUID and PGID instead.")
    if UID == 0 or GID == 0:
        sys.exit("Set PUID and PGID to a user and group other than root (on Unraid: 99 and 100).")
    tz = os.environ.get("TZ") or "UTC"

    log("setup: secrets, folders and the built data")
    r = subprocess.run([sys.executable, "-I", "/app/docker/setup.py"], env={**os.environ, "PUID": str(UID), "PGID": str(GID)})
    if r.returncode:
        sys.exit(r.returncode)
    nginx_config()

    base = {k: v for k, v in os.environ.items() if k not in ("HOST", "PORT")}
    services = {
        # SANGUITY_DEPLOYMENT: how it's deployed, for Settings → General → About (#1004); Unraid's template sets its own.
        # TZ: the receiver's time zone, which General → Phone receiver shows (#998)
        "app": (["python3", "-m", "server"], {"SANGUITY_DEPLOYMENT": "allinone", **base, "TZ": tz, "HOST": "127.0.0.1", "PORT": "8000",
                                               "FORWARDED_ALLOW_IPS": "127.0.0.1"}),
        "web": (["nginx", "-e", "stderr", "-c", os.path.join(NGINX_TMP, "nginx.conf"), "-g", "daemon off;"], base),
        "receiver": (["python3", "-I", "/app/receiver.py"], {**base, "DATA": WATCH, "PORT": "8081", "TZ_DEFAULT": tz,
                                                          "SECRET_FILE": os.path.join(AUTH, "watch_secret")}),
    }
    procs = {}
    for name, (cmd, env) in services.items():
        procs[name] = subprocess.Popen(cmd, env=env, cwd="/app", user=UID, group=GID, extra_groups=[])
        log(f"started {name} as {UID}:{GID}")

    stopping = []

    def stop(signum, frame):
        stopping.append(signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    code = 0
    while not stopping:
        ended = [(n, p.returncode) for n, p in procs.items() if p.poll() is not None]
        if ended:
            name, code = ended[0]
            log(f"{name} stopped (exit code {code}); stopping the rest")
            code = code or 1
            break
        time.sleep(1)
    for p in procs.values():
        if p.poll() is None:
            p.terminate()
    deadline = time.time() + 20
    for p in procs.values():
        try:
            p.wait(max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(code)


if __name__ == "__main__":
    main()
